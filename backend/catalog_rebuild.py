"""
Catalog rebuild engine.
Scans tapes and reconstructs the database from embedded catalogs.
"""
import json
import logging
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from backend.catalog_security import verify_catalog, get_trust_level

logger = logging.getLogger(__name__)


class CatalogRebuildEngine:
    """Rebuilds database catalog from tape metadata."""
    
    def __init__(self, db, tape_controller):
        self.db = db
        self.tape_controller = tape_controller
        self.discovered_catalogs = []
        self.trust_results = {}
    
    def scan_tape_for_catalog(self, tape_barcode: str) -> Optional[Dict]:
        """
        Load tape, read FOSSILSAFE_CATALOG.json, verify signature.
        
        Returns:
            Catalog dict if found, None otherwise
        """
        try:
            logger.info(f"Scanning tape {tape_barcode} for catalog")
            
            # Load and mount tape
            success, mount_point = self.tape_controller.load_and_mount_tape(tape_barcode)
            if not success:
                logger.error(f"Failed to mount tape {tape_barcode}: {mount_point}")
                return None
            
            # Look for catalog file
            catalog_path = Path(mount_point) / "FOSSILSAFE_CATALOG.json"
            if not catalog_path.exists():
                logger.warning(f"No catalog found on tape {tape_barcode} (legacy tape)")
                self.tape_controller.unmount_tape(tape_barcode)
                return None
            
            # Read and parse catalog
            with open(catalog_path, 'r') as f:
                catalog_data = json.load(f)
            
            # Verify signature
            is_valid, message = verify_catalog(catalog_data)
            trust_level = get_trust_level(catalog_data)
            
            # Persist trust status
            try:
                self.db.update_tape_trust_status(tape_barcode, trust_level)
            except Exception as e:
                logger.warning(f"Failed to update trust status for {tape_barcode}: {e}")

            self.trust_results[tape_barcode] = {
                'valid': is_valid,
                'message': message,
                'trust_level': trust_level
            }
            
            logger.info(f"Tape {tape_barcode}: {message} (trust: {trust_level})")
            
            # Unmount tape
            self.tape_controller.unmount_tape(tape_barcode)
            
            return catalog_data
            
        except Exception as e:
            logger.exception(f"Error scanning tape {tape_barcode}")
            return None
    
    def rebuild_from_tapes(self, tape_barcodes: List[str]) -> Dict:
        """
        Scan multiple tapes and rebuild catalog.
        
        Returns:
            Summary dict with statistics
        """
        logger.info(f"Starting catalog rebuild from {len(tape_barcodes)} tapes")
        
        self.discovered_catalogs = []
        self.trust_results = {}
        
        # Scan all tapes
        for barcode in tape_barcodes:
            catalog = self.scan_tape_for_catalog(barcode)
            if catalog:
                self.discovered_catalogs.append(catalog)
        
        # Group by backup sets
        backup_sets = {}
        for catalog in self.discovered_catalogs:
            backup_set_id = catalog.get('backup_set_id')
            if backup_set_id not in backup_sets:
                backup_sets[backup_set_id] = []
            backup_sets[backup_set_id].append(catalog)
        
        # Verify chain of trust for multi-volume sets
        for backup_set_id, catalogs in backup_sets.items():
            if len(catalogs) > 1:
                self._verify_chain_of_trust(catalogs)
        
        # Calculate statistics
        total_files = sum(cat.get('total_files', 0) for cat in self.discovered_catalogs)
        total_bytes = sum(cat.get('total_bytes', 0) for cat in self.discovered_catalogs)
        
        trusted_count = sum(1 for r in self.trust_results.values() if r['trust_level'] == 'trusted')
        partial_count = sum(1 for r in self.trust_results.values() if r['trust_level'] == 'partial')
        untrusted_count = sum(1 for r in self.trust_results.values() if r['trust_level'] == 'untrusted')
        
        summary = {
            'tapes_scanned': len(tape_barcodes),
            'catalogs_found': len(self.discovered_catalogs),
            'backup_sets': len(backup_sets),
            'total_files': total_files,
            'total_bytes': total_bytes,
            'trust_summary': {
                'trusted': trusted_count,
                'partial': partial_count,
                'untrusted': untrusted_count
            }
        }
        
        logger.info(f"Rebuild complete: {summary}")
        return summary
    
    def _verify_chain_of_trust(self, catalogs: List[Dict]) -> bool:
        """Verify chain of trust across multi-volume set."""
        # Sort by volume number
        sorted_catalogs = sorted(catalogs, key=lambda c: c.get('tape_sequence', {}).get('volume_number', 0))
        
        for i in range(1, len(sorted_catalogs)):
            current = sorted_catalogs[i]
            previous = sorted_catalogs[i-1]
            
            current_chain = current.get('security', {}).get('chain_of_trust', {})
            prev_hash = current_chain.get('previous_catalog_hash', '')
            
            # Calculate hash of previous catalog
            prev_security = previous.get('security', {})
            expected_hash = prev_security.get('catalog_hash', '')
            
            if prev_hash and prev_hash != expected_hash:
                logger.warning(f"Chain of trust broken between volumes {i} and {i+1}")
                return False
        
        logger.info(f"Chain of trust verified for {len(catalogs)} volumes")
        return True
    
    def import_to_database(self) -> int:
        """
        Import discovered catalogs into database.
        
        Returns:
            Number of files imported
        """
        if not self.discovered_catalogs:
            logger.warning("No catalogs to import")
            return 0
        
        total_imported = 0
        
        for catalog in self.discovered_catalogs:
            try:
                backup_set_id = catalog['backup_set_id']
                job_id = catalog.get('job_id', 0)
                sources = catalog.get('sources', [])
                
                # Add backup set
                self.db.add_backup_set(backup_set_id, sources)
                
                # Add files
                for file_entry in catalog.get('files', []):
                    self.db.execute("""
                        INSERT OR IGNORE INTO archived_files 
                        (file_path, file_size, file_mtime, checksum, tape_barcode, backup_set_id)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        file_entry['path'],
                        file_entry['size'],
                        file_entry['date'],
                        file_entry['checksum'],
                        catalog['tape_barcode'],
                        backup_set_id
                    ))
                    total_imported += 1
                
                self.db.commit()
                logger.info(f"Imported {len(catalog.get('files', []))} files from tape {catalog['tape_barcode']}")
                
            except Exception as e:
                logger.exception(f"Failed to import catalog: {e}")
                self.db.rollback()
        
        return total_imported

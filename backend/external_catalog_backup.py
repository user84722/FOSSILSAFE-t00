"""
External catalog backup service.
Periodically backs up the entire catalog to dedicated external tapes for off-site storage.
"""
import json
import logging
import os
import shutil
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from backend.catalog_security import sign_catalog
from backend import config_store
from backend.smb_client import SMBClient

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    boto3 = None
    ClientError = None

logger = logging.getLogger(__name__)


class ExternalCatalogBackup:
    """Manages external catalog backups to dedicated tapes."""
    
    def __init__(self, db, tape_controller):
        self.db = db
        self.tape_controller = tape_controller
    
    def create_full_catalog_export(self) -> Dict:
        """
        Export entire catalog to a single JSON structure.
        
        Returns:
            Complete catalog export with all backup sets, files, and metadata
        """
        logger.info("Creating full catalog export")
        
        # Get all backup sets
        backup_sets = self.db.execute("""
            SELECT id as backup_set_id, sources, created_at
            FROM backup_sets
            ORDER BY created_at DESC
        """).fetchall()
        
        export = {
            'export_version': '1.0',
            'export_date': datetime.utcnow().isoformat() + 'Z',
            'appliance_info': self._get_appliance_info(),
            'backup_sets': [],
            'total_files': 0,
            'total_bytes': 0
        }
        
        for backup_set in backup_sets:
            backup_set_id = backup_set['backup_set_id']
            
            # Get all snapshots for this backup set
            snapshots = self.db.execute("""
                SELECT id, job_id, created_at, total_files, total_bytes
                FROM backup_snapshots
                WHERE backup_set_id = ?
                ORDER BY created_at DESC
            """, (backup_set_id,)).fetchall()
            
            # Get all files for this backup set
            files = self.db.execute("""
                SELECT file_path, file_size, file_mtime, checksum, tape_barcode
                FROM archived_files
                WHERE backup_set_id = ?
            """, (backup_set_id,)).fetchall()
            
            backup_set_data = {
                'backup_set_id': backup_set_id,
                'sources': json.loads(backup_set['sources']) if isinstance(backup_set['sources'], str) else backup_set['sources'],
                'created_at': backup_set['created_at'],
                'snapshots': [dict(s) for s in snapshots],
                'files': [
                    {
                        'path': f['file_path'],
                        'size': f['file_size'],
                        'mtime': f['file_mtime'],
                        'checksum': f['checksum'],
                        'tape_barcode': f['tape_barcode']
                    }
                    for f in files
                ],
                'file_count': len(files),
                'total_bytes': sum(f['file_size'] for f in files)
            }
            
            export['backup_sets'].append(backup_set_data)
            export['total_files'] += len(files)
            export['total_bytes'] += backup_set_data['total_bytes']
        
        logger.info(f"Catalog export complete: {len(backup_sets)} backup sets, {export['total_files']} files")
        return export
    
    def _get_appliance_info(self) -> Dict:
        """Get appliance identification info."""
        try:
            import socket
            hostname = socket.gethostname()
        except:
            hostname = 'unknown'
        
        return {
            'hostname': hostname,
            'export_type': 'full_catalog',
            'fossilsafe_version': '1.0'  # TODO: Get from version file
        }
    
    def write_catalog_to_tape(self, tape_barcode: str) -> Tuple[bool, str]:
        """
        Write full catalog export to external tape.
        Also includes the raw SQLite database and configuration for Disaster Recovery.
        
        Args:
            tape_barcode: Barcode of tape to write catalog to
            
        Returns:
            (success, message)
        """
        try:
            logger.info(f"Writing external catalog backup to tape {tape_barcode}")
            
            # Load and mount tape
            success, mount_point = self.tape_controller.load_and_mount_tape(tape_barcode)
            if not success:
                return False, f"Failed to mount tape: {mount_point}"
            
            # 1. DR: Copy raw database
            try:
                if hasattr(self.db, 'db_path') and os.path.exists(self.db.db_path):
                    logger.info(f"Copying database from {self.db.db_path} to tape")
                    
                    # Force a checkpoint to ensure WAL is merged or at least safe
                    try:
                        self.db.execute("PRAGMA wal_checkpoint(FULL)")
                    except Exception as e:
                        logger.warning(f"Failed to checkpoint WAL before backup: {e}")
                        
                    shutil.copy2(self.db.db_path, os.path.join(mount_point, 'fossilsafe_dr.db'))
                    
                    # Also copy WAL/SHM if they exist, just in case
                    wal_path = f"{self.db.db_path}-wal"
                    shutil.copy2(wal_path, os.path.join(mount_point, 'fossilsafe_dr.db-wal')) if os.path.exists(wal_path) else None
                    shutil.copy2(f"{self.db.db_path}-shm", os.path.join(mount_point, 'fossilsafe_dr.db-shm')) if os.path.exists(f"{self.db.db_path}-shm") else None
                else:
                    logger.warning("Database path not found, skipping DB dump")
            except Exception as e:
                logger.error(f"Failed to copy database file: {e}")
                # We continue, as JSON export is still valuable
                
            # 2. DR: Copy configuration
            try:
                config_path = config_store.get_config_path()
                if os.path.exists(config_path):
                    logger.info(f"Copying config from {config_path} to tape")
                    shutil.copy2(config_path, os.path.join(mount_point, 'config_backup.json'))
            except Exception as e:
                logger.error(f"Failed to copy config file: {e}")

            # 3. Standard JSON Catalog Export
            # Create catalog export
            catalog_export = self.create_full_catalog_export()
            
            # Sign the export
            try:
                signed_export = sign_catalog(catalog_export)
            except Exception as e:
                logger.warning(f"Failed to sign catalog export: {e}")
                signed_export = catalog_export
            
            # Write to tape
            catalog_path = os.path.join(mount_point, 'FOSSILSAFE_EXTERNAL_CATALOG.json')
            with open(catalog_path, 'w') as f:
                json.dump(signed_export, f, indent=2)
            
            # Write metadata file
            metadata = {
                'backup_type': 'external_catalog_dr',
                'created_at': datetime.utcnow().isoformat() + 'Z',
                'tape_barcode': tape_barcode,
                'total_backup_sets': len(catalog_export['backup_sets']),
                'total_files': catalog_export['total_files'],
                'total_bytes': catalog_export['total_bytes'],
                'dr_included': True
            }
            
            metadata_path = os.path.join(mount_point, 'CATALOG_METADATA.json')
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            # Unmount tape
            self.tape_controller.unmount_tape(tape_barcode)
            
            # Record in database
            self.db.execute("""
                INSERT INTO external_catalog_backups 
                (tape_barcode, created_at, backup_sets_count, files_count, bytes_total)
                VALUES (?, ?, ?, ?, ?)
            """, (
                tape_barcode,
                datetime.utcnow().isoformat(),
                len(catalog_export['backup_sets']),
                catalog_export['total_files'],
                catalog_export['total_bytes']
            ))
            self.db.commit()
            
            logger.info(f"External catalog backup complete: {catalog_export['total_files']} files on tape {tape_barcode}")
            
            # --- NEW: Cloud Sync Integration ---
            try:
                self.sync_catalog_to_cloud(catalog_path)
            except Exception as e:
                logger.error(f"Cloud sync failed after tape export: {e}")
                
            return True, f"Catalog backed up to tape {tape_barcode}"
            
        except Exception as e:
            logger.exception("Failed to write external catalog backup")
            try:
                self.tape_controller.unmount_tape(tape_barcode)
            except:
                pass
            return False, str(e)
    
    def restore_from_external_catalog(self, tape_barcode: str) -> Tuple[bool, str, Optional[Dict]]:
        """
        Read and verify external catalog backup from tape.
        
        Args:
            tape_barcode: Barcode of external catalog tape
            
        Returns:
            (success, message, catalog_data)
        """
        try:
            logger.info(f"Reading external catalog from tape {tape_barcode}")
            
            # Load and mount tape
            success, mount_point = self.tape_controller.load_and_mount_tape(tape_barcode)
            if not success:
                return False, f"Failed to mount tape: {mount_point}", None
            
            # Read catalog
            catalog_path = os.path.join(mount_point, 'FOSSILSAFE_EXTERNAL_CATALOG.json')
            if not os.path.exists(catalog_path):
                self.tape_controller.unmount_tape(tape_barcode)
                return False, "No external catalog found on tape", None
            
            with open(catalog_path, 'r') as f:
                catalog_data = json.load(f)
            
            # Verify signature
            from backend.catalog_security import verify_catalog, get_trust_level
            is_valid, message = verify_catalog(catalog_data)
            trust_level = get_trust_level(catalog_data)
            
            # Persist trust status
            try:
                self.db.update_tape_trust_status(tape_barcode, trust_level)
            except Exception as e:
                logger.warning(f"Failed to update trust status for {tape_barcode}: {e}")
            
            # Unmount tape
            self.tape_controller.unmount_tape(tape_barcode)
            
            logger.info(f"External catalog read: {message} (trust: {trust_level})")
            return True, f"Catalog verified: {message}", catalog_data
            
        except Exception as e:
            logger.exception("Failed to read external catalog")
            try:
                self.tape_controller.unmount_tape(tape_barcode)
            except:
                pass
            return False, str(e), None
    
    def import_external_catalog(self, catalog_data: Dict) -> Tuple[bool, str]:
        """
        Import external catalog into database.
        
        Args:
            catalog_data: Catalog export data
            
        Returns:
            (success, message)
        """
        try:
            logger.info("Importing external catalog to database")
            
            imported_files = 0
            imported_sets = 0
            
            for backup_set in catalog_data.get('backup_sets', []):
                backup_set_id = backup_set['backup_set_id']
                sources = backup_set['sources']
                
                # Add backup set
                self.db.execute("""
                    INSERT OR IGNORE INTO backup_sets (id, sources, created_at)
                    VALUES (?, ?, ?)
                """, (backup_set_id, json.dumps(sources), backup_set['created_at']))
                
                # Add files
                for file_entry in backup_set.get('files', []):
                    self.db.execute("""
                        INSERT OR IGNORE INTO archived_files 
                        (file_path, file_size, file_mtime, checksum, tape_barcode, backup_set_id)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        file_entry['path'],
                        file_entry['size'],
                        file_entry['mtime'],
                        file_entry['checksum'],
                        file_entry['tape_barcode'],
                        backup_set_id
                    ))
                    imported_files += 1
                
                imported_sets += 1
            
            self.db.commit()
            
            logger.info(f"External catalog import complete: {imported_sets} sets, {imported_files} files")
            return True, f"Imported {imported_sets} backup sets with {imported_files} files"
            
        except Exception as e:
            logger.exception("Failed to import external catalog")
            self.db.rollback()
            return False, str(e)

    def sync_without_tape(self) -> Tuple[bool, str, List[str]]:
        """
        Export catalog to a temporary local file and sync to cloud.
        Used for periodic cloud-only catalog backups.
        
        Returns:
            (success, message, cloud_results)
        """
        temp_path = f"/tmp/fossilsafe_catalog_sync_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            # 1. Export
            catalog_export = self.create_full_catalog_export()
            
            # 2. Sign
            try:
                from backend.catalog_security import sign_catalog
                signed_export = sign_catalog(catalog_export)
            except Exception as e:
                logger.warning(f"Failed to sign catalog export: {e}")
                signed_export = catalog_export
                
            # 3. Write temp file
            with open(temp_path, 'w') as f:
                json.dump(signed_export, f)
            
            # 4. Sync
            results = self.sync_catalog_to_cloud(temp_path)
            
            # 5. Record
            self.db.execute("""
                INSERT INTO external_catalog_backups 
                (tape_barcode, created_at, backup_sets_count, files_count, bytes_total)
                VALUES (?, ?, ?, ?, ?)
            """, (
                "CLOUD_SYNC",
                datetime.utcnow().isoformat(),
                len(catalog_export['backup_sets']),
                catalog_export['total_files'],
                catalog_export['total_bytes']
            ))
            self.db.commit()
            
            return True, "Cloud sync complete", results
            
        except Exception as e:
            logger.exception("Cloud-only sync failed")
            return False, str(e), []
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def sync_catalog_to_cloud(self, export_path: str) -> List[str]:
        """
        Sync the exported catalog to configured cloud destinations.
        Respects license capabilities (Cloud Sync is Pro/Enterprise).
        
        Args:
            export_path: Path to the JSON export file
            
        Returns:
            List of success messages
        """
        results = []
        config = config_store.load_config()
        cloud_config = config.get('cloud_sync', {})
        
        if not cloud_config.get('enabled'):
            return []
            
        # License Check
        if False: # Fully enabled in AGPL-3.0
            logger.warning("Cloud Sync enabled but license missing 'cloud_sync' capability. Skipping.")
            return ["Skipped Cloud Sync (License Required)"]

        # S3 Sync
        if cloud_config.get('s3', {}).get('enabled'):
            try:
                if self.upload_to_s3(cloud_config['s3'], export_path):
                    results.append("Uploaded to S3")
            except Exception as e:
                logger.error(f"S3 Upload failed: {e}")
                results.append(f"S3 Upload Failed: {e}")
                
        # SMB Sync
        if cloud_config.get('smb', {}).get('enabled'):
            try:
                if self.upload_to_smb(cloud_config['smb'], export_path):
                    results.append("Uploaded to SMB")
            except Exception as e:
                logger.error(f"SMB Upload failed: {e}")
                results.append(f"SMB Upload Failed: {e}")
                
        return results

    def upload_to_s3(self, s3_config: Dict, file_path: str) -> bool:
        """Upload file to S3/MinIO."""
        if not boto3:
            logger.error("boto3 not installed, cannot upload to S3")
            return False
            
        endpoint_url = s3_config.get('endpoint_url') # Optional for AWS, required for MinIO
        access_key = s3_config.get('access_key')
        secret_key = s3_config.get('secret_key')
        bucket = s3_config.get('bucket')
        region = s3_config.get('region', 'us-east-1')
        
        if not all([access_key, secret_key, bucket]):
            logger.error("Incomplete S3 configuration")
            return False
            
        s3_client = boto3.client(
            's3',
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region
        )
        
        filename = os.path.basename(file_path)
        # Use a timestamped prefix or folder?
        # Let's use 'catalog_backups/YYYY-MM-DD timestamp_filename'
        timestamp = datetime.now().strftime('%Y-%m-%d')
        key = f"catalog_backups/{timestamp}/{filename}"
        
        logger.info(f"Uploading {file_path} to s3://{bucket}/{key}")
        s3_client.upload_file(file_path, bucket, key)
        return True

    def upload_to_smb(self, smb_config: Dict, file_path: str) -> bool:
        """Upload file to SMB share."""
        host = smb_config.get('host')
        share = smb_config.get('share')
        username = smb_config.get('username')
        password = smb_config.get('password')
        domain = smb_config.get('domain', '')
        path = smb_config.get('path', '') # Subdirectory
        
        if not all([host, share, username, password]):
            logger.error("Incomplete SMB configuration")
            return False
            
        share_path = f"//{host}/{share}"
        filename = os.path.basename(file_path)
        
        # Construct remote path
        # Note: smbclient expects backslashes for paths inside the share
        if path:
            dest_path = os.path.join(path, filename).replace('/', '\\')
        else:
            dest_path = filename
            
        logger.info(f"Uploading {file_path} to {share_path}/{dest_path}")
        
        client = SMBClient()
        return client.upload_file(
            share_path=share_path,
            username=username,
            password=password,
            local_file=file_path,
            remote_file=dest_path,
            domain=domain
        )

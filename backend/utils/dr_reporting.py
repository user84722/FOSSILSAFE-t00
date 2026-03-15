import json
import os
from datetime import datetime
from typing import Dict, List

class DRReportGenerator:
    """Utility to generate Disaster Recovery Verification Reports."""
    
    @staticmethod
    def generate_json_report(results: Dict, output_path: str):
        """Generate a raw JSON report for machine consumption."""
        report = {
            'report_type': 'DR_VERIFICATION_SIMULATION',
            'generated_at': datetime.utcnow().isoformat() + 'Z',
            'appliance_id': os.uname().nodename,
            'summary': {
                'barcode': results.get('barcode'),
                'status': results.get('status'),
                'catalog_verified': results.get('catalog_verified'),
                'files_total': results.get('files_verified', 0) + results.get('files_failed', 0),
                'files_verified': results.get('files_verified', 0),
                'files_failed': results.get('files_failed', 0)
            },
            'details': results.get('errors', [])
        }
        
        with open(output_path, 'w') as f:
            json.dump(report, f, indent=4)
        return output_path

    @staticmethod
    def generate_text_summary(results: Dict) -> str:
        """Generate a human-readable text summary of the verification."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status_icon = "✅" if results.get('status') == 'completed' else "⚠️" if results.get('status') == 'warning' else "❌"
        
        lines = [
            "====================================================",
            "      FOSSILSAFE DISASTER RECOVERY REPORT           ",
            "====================================================",
            f"Generated: {timestamp}",
            f"Tape ID:   {results.get('barcode', 'UNKNOWN')}",
            f"Status:    {status_icon} {results.get('status', 'failed').upper()}",
            "----------------------------------------------------",
            f"Catalog Signature: {'VALID' if results.get('catalog_verified') else 'INVALID/MISSING'}",
            f"Files Verified:    {results.get('files_verified', 0)}",
            f"Files Failed:      {results.get('files_failed', 0)}",
            "----------------------------------------------------"
        ]
        
        if results.get('errors'):
            lines.append("ERRORS/WARNINGS:")
            for error in results['errors']:
                lines.append(f"- {error}")
            lines.append("----------------------------------------------------")
            
        if results.get('status') == 'completed':
            lines.append("CONCLUSION: TAPE ARCHIVE IS CRYPTOGRAPHICALLY SOUND AND RECOVERABLE.")
        else:
            lines.append("CONCLUSION: INTEGRITY ISSUES DETECTED. RECOVERY MAY BE PARTIAL OR AT RISK.")
            
        lines.append("====================================================")
        return "\n".join(lines)

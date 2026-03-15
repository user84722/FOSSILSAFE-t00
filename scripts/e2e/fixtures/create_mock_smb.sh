#!/bin/bash
# Create mock SMB share directory structure for testing

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOCK_SMB_DIR="${SCRIPT_DIR}/fixtures/mock_smb_share"

echo "=== Creating Mock SMB Share ==="

# Create directory structure
mkdir -p "${MOCK_SMB_DIR}"

# Create sample files and directories
echo "Creating sample directory structure..."

# Documents folder
mkdir -p "${MOCK_SMB_DIR}/Documents"
echo "Sample document 1" > "${MOCK_SMB_DIR}/Documents/report.txt"
echo "Sample document 2" > "${MOCK_SMB_DIR}/Documents/memo.txt"

# Spreadsheets folder
mkdir -p "${MOCK_SMB_DIR}/Spreadsheets"
echo "Sample spreadsheet data" > "${MOCK_SMB_DIR}/Spreadsheets/budget.csv"
echo "Sample spreadsheet data 2" > "${MOCK_SMB_DIR}/Spreadsheets/inventory.csv"

# Images folder
mkdir -p "${MOCK_SMB_DIR}/Images"
# Create small binary files to simulate images
dd if=/dev/urandom of="${MOCK_SMB_DIR}/Images/photo1.jpg" bs=1024 count=10 2>/dev/null
dd if=/dev/urandom of="${MOCK_SMB_DIR}/Images/photo2.jpg" bs=1024 count=10 2>/dev/null

# Nested structure
mkdir -p "${MOCK_SMB_DIR}/Projects/2024/Q1"
mkdir -p "${MOCK_SMB_DIR}/Projects/2024/Q2"
echo "Q1 project notes" > "${MOCK_SMB_DIR}/Projects/2024/Q1/notes.txt"
echo "Q2 project notes" > "${MOCK_SMB_DIR}/Projects/2024/Q2/notes.txt"

# Archive folder
mkdir -p "${MOCK_SMB_DIR}/Archive/Old_Documents"
echo "Archived document" > "${MOCK_SMB_DIR}/Archive/Old_Documents/old_report.txt"

# Create .smbshare marker file
cat > "${MOCK_SMB_DIR}/.smbshare" <<EOF
# Mock SMB Share
# This directory simulates an SMB share for testing purposes
# Created: $(date)
EOF

# Create README
cat > "${MOCK_SMB_DIR}/README.txt" <<EOF
Mock SMB Share for Testing
===========================

This directory structure simulates an SMB share for E2E testing.

Structure:
- Documents/       - Sample text documents
- Spreadsheets/    - Sample CSV files
- Images/          - Sample binary files
- Projects/        - Nested directory structure
- Archive/         - Archived files

Total files: $(find "${MOCK_SMB_DIR}" -type f | wc -l)
Total size: $(du -sh "${MOCK_SMB_DIR}" | cut -f1)

Created: $(date)
EOF

# Set permissions
chmod -R 755 "${MOCK_SMB_DIR}"

# Summary
echo ""
echo "✅ Mock SMB share created at: ${MOCK_SMB_DIR}"
echo ""
echo "Statistics:"
echo "  Files: $(find "${MOCK_SMB_DIR}" -type f | wc -l)"
echo "  Directories: $(find "${MOCK_SMB_DIR}" -type d | wc -l)"
echo "  Total size: $(du -sh "${MOCK_SMB_DIR}" | cut -f1)"
echo ""
echo "To use in tests:"
echo "  export MOCK_SMB_PATH='${MOCK_SMB_DIR}'"
echo ""

exit 0

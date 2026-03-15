#!/usr/bin/env bash
# Generate deterministic test datasets for E2E tests

set -euo pipefail

FIXTURES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Generating E2E Test Fixtures ==="
echo "Output directory: $FIXTURES_DIR"
echo ""

# Create small dataset (10 files, ~1MB total)
create_small_dataset() {
  echo "[1/3] Creating small dataset (10 files, 1MB)..."
  local temp_dir="/tmp/test_dataset_small"
  rm -rf "$temp_dir"
  mkdir -p "$temp_dir/subdir1" "$temp_dir/subdir2"
  
  # Create files with deterministic content
  for i in $(seq 1 10); do
    local size_kb=$((100))  # 100KB each
    local file="$temp_dir/file_$i.bin"
    if [ $((i % 3)) -eq 0 ]; then
      file="$temp_dir/subdir1/file_$i.bin"
    elif [ $((i % 2)) -eq 0 ]; then
      file="$temp_dir/subdir2/file_$i.bin"
    fi
    dd if=/dev/zero of="$file" bs=1024 count="$size_kb" 2>/dev/null
    echo "Test file $i - $(date -Iseconds)" > "$file.txt"
  done
  
  tar -czf "$FIXTURES_DIR/test_dataset_small.tar.gz" -C /tmp test_dataset_small
  rm -rf "$temp_dir"
  echo "   Created: test_dataset_small.tar.gz"
}

# Create medium dataset (100 files, ~10MB total)
create_medium_dataset() {
  echo "[2/3] Creating medium dataset (100 files, 10MB)..."
  local temp_dir="/tmp/test_dataset_medium"
  rm -rf "$temp_dir"
  mkdir -p "$temp_dir/level1/level2/level3"
  
  for i in $(seq 1 100); do
    local size_kb=$((100))  # 100KB each
    local file="$temp_dir/file_$i.bin"
    
    # Distribute across nested directories
    if [ $((i % 10)) -eq 0 ]; then
      file="$temp_dir/level1/level2/level3/file_$i.bin"
    elif [ $((i % 5)) -eq 0 ]; then
      file="$temp_dir/level1/level2/file_$i.bin"
    elif [ $((i % 2)) -eq 0 ]; then
      file="$temp_dir/level1/file_$i.bin"
    fi
    
    dd if=/dev/zero of="$file" bs=1024 count="$size_kb" 2>/dev/null
    echo "Test file $i - $(date -Iseconds)" > "$file.txt"
  done
  
  tar -czf "$FIXTURES_DIR/test_dataset_medium.tar.gz" -C /tmp test_dataset_medium
  rm -rf "$temp_dir"
  echo "   Created: test_dataset_medium.tar.gz"
}

# Create large dataset (1000 files, ~100MB total)
create_large_dataset() {
  echo "[3/3] Creating large dataset (1000 files, 100MB)..."
  local temp_dir="/tmp/test_dataset_large"
  rm -rf "$temp_dir"
  mkdir -p "$temp_dir"
  
  # Create directory structure
  for dir in $(seq 1 10); do
    mkdir -p "$temp_dir/dir_$dir"
  done
  
  for i in $(seq 1 1000); do
    local size_kb=$((100))  # 100KB each
    local dir_num=$((i % 10 + 1))
    local file="$temp_dir/dir_$dir_num/file_$i.bin"
    
    dd if=/dev/zero of="$file" bs=1024 count="$size_kb" 2>/dev/null
    
    # Add some text files too
    if [ $((i % 10)) -eq 0 ]; then
      echo "Test file $i - $(date -Iseconds)" > "$temp_dir/dir_$dir_num/file_$i.txt"
    fi
  done
  
  tar -czf "$FIXTURES_DIR/test_dataset_large.tar.gz" -C /tmp test_dataset_large
  rm -rf "$temp_dir"
  echo "   Created: test_dataset_large.tar.gz"
}

# Create mock tape catalog
create_mock_catalog() {
  echo "[4/4] Creating mock tape catalog..."
  cat > "$FIXTURES_DIR/mock_tape_catalog.json" <<'EOF'
{
  "version": "1.0",
  "generated_at": "2026-02-10T00:00:00Z",
  "tape_barcode": "TEST001L6",
  "backup_job_id": "mock-job-123",
  "files": [
    {
      "id": 1,
      "path": "/test/file1.txt",
      "size": 1024,
      "mtime": "2026-02-09T12:00:00Z",
      "checksum": "abc123def456",
      "tape_position": 0
    },
    {
      "id": 2,
      "path": "/test/file2.bin",
      "size": 102400,
      "mtime": "2026-02-09T12:01:00Z",
      "checksum": "def456abc789",
      "tape_position": 1024
    }
  ],
  "signature": "mock_ed25519_signature",
  "trust_level": "fully_trusted"
}
EOF
  echo "   Created: mock_tape_catalog.json"
}

# Main execution
main() {
  create_small_dataset
  create_medium_dataset
  create_large_dataset
  create_mock_catalog
  
  echo ""
  echo "=== Fixtures Summary ==="
  ls -lh "$FIXTURES_DIR"/*.tar.gz "$FIXTURES_DIR"/*.json 2>/dev/null || true
  echo ""
  echo "Fixtures generated successfully!"
}

main


import os
import sys
import shutil
import pytest
import time

# Add backend to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.tape.vtl import VirtualTapeController

@pytest.fixture
def vtl_env():
    # Setup
    root = "/tmp/test_vtl"
    if os.path.exists(root):
        shutil.rmtree(root)
    os.makedirs(root)
    os.environ['VTL_ROOT'] = root
    os.environ['VTL_DRIVES'] = '2'
    os.environ['VTL_SLOTS'] = '10'
    os.environ['VTL_DRIVE_ONLY'] = '0'
    
    yield root
    
    # Teardown
    if os.path.exists(root):
        shutil.rmtree(root)

def test_vtl_initialization(vtl_env):
    vtl = VirtualTapeController()
    vtl.initialize()
    inventory = vtl.scan_barcodes()
    assert len(inventory) > 0
    # verify slots created
    slots = [i for i in inventory if i['type'] == 'slot']
    assert len(slots) > 0
    print("VTL Initialized with inventory size:", len(inventory))

def test_vtl_lifecycle(vtl_env):
    vtl = VirtualTapeController()
    vtl.initialize()
    
    # Get a tape
    inventory = vtl.scan_barcodes()
    # Find a slot with a barcode
    tape = next((i for i in inventory if i['type'] == 'slot' and i.get('barcode')), None)
    assert tape is not None, "No tapes found in VTL"
    
    barcode = tape['barcode']
    print(f"Testing with tape {barcode}")
    
    # Load
    print("Loading tape...")
    vtl.load_tape(barcode, drive=0)
    inventory = vtl.scan_barcodes()
    drive_item = next(i for i in inventory if i['type'] == 'drive' and i['drive_index'] == 0)
    assert drive_item['barcode'] == barcode
    
    # Format
    print("Formatting tape...")
    vtl.format_tape(drive=0, label="TEST01")
    
    # Mount
    print("Mounting tape...")
    mount_point = vtl.mount_ltfs(barcode, drive=0)
    assert os.path.exists(mount_point)
    assert os.path.islink(mount_point) or os.path.isdir(mount_point)
    
    # Write file
    print("Writing verification file...")
    test_file = os.path.join(mount_point, "test.txt")
    with open(test_file, 'w') as f:
        f.write("Hello VTL")
    
    assert os.path.exists(test_file)
    with open(test_file, 'r') as f:
        content = f.read()
    assert content == "Hello VTL"
    
    # Unmount
    print("Unmounting tape...")
    vtl.unmount_ltfs(drive=0)
    
    # Unload
    print("Unloading tape...")
    vtl.unload_tape(drive=0)
    inventory = vtl.scan_barcodes()
    drive_item = next(i for i in inventory if i['type'] == 'drive' and i['drive_index'] == 0)
    assert drive_item.get('barcode') is None
    
    print("VTL Lifecycle Test Passed")

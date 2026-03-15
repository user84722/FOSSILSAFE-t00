import logging
from typing import Dict, List, Optional
from backend.tape_controller import TapeLibraryController
from backend.config_store import load_config, load_state

logger = logging.getLogger(__name__)

class LibraryManager:
    """
    Manages multiple TapeLibraryController instances.
    Supports legacy single-library configuration and new multi-library configuration.
    """

    def __init__(self, db, event_logger=None):
        self.db = db
        self.event_logger = event_logger
        self.controllers: Dict[str, TapeLibraryController] = {}
        self.default_library_id: Optional[str] = None

    def initialize(self):
        """Load configuration and initialize library controllers."""
        config = load_config()
        # self.controllers.clear() # Preserved manually registered controllers

        # 1. Check for Multi-Library Config
        libraries_config = config.get('libraries')
        if isinstance(libraries_config, list) and len(libraries_config) > 0:
            logger.info(f"Found {len(libraries_config)} libraries in configuration.")
            for lib_conf in libraries_config:
                self._init_library_from_config(lib_conf)
        elif 'default' not in self.controllers:
            # 2. Fallback to Legacy/Single Config ONLY if not already registered manually
            logger.info("No 'libraries' config found. Falling back to legacy single-library mode.")
            self._init_legacy_library(config)
        else:
            logger.info("LibraryManager using existing 'default' controller.")

        if not self.controllers:
             logger.warning("No tape libraries configured.")

    def _init_library_from_config(self, config: Dict):
        lib_id = config.get('id')
        if not lib_id:
            logger.error("Library config missing 'id'. Skipping.")
            return

        device = config.get('device') or config.get('drive_device') or '/dev/nst0'
        changer = config.get('changer') or config.get('changer_device') or '/dev/sg1'
        
        # Handle mapped drives dict if present
        drive_devices = config.get('drive_devices') 
        if isinstance(drive_devices, list):
             # Convert list [path1, path2] to dict {0: path1, 1: path2}
             device = {i: path for i, path in enumerate(drive_devices)}

        try:
            controller = TapeLibraryController(
                device=device,
                changer=changer,
                config=load_config(), # Pass full config for global settings
                state=load_state(),   # State management might need scoping per library later
                event_logger=self.event_logger,
                db=self.db
            )
            # Inject library ID into controller if we add that support later
            setattr(controller, 'library_id', lib_id)
            
            controller.initialize()
            self.controllers[lib_id] = controller
            logger.info(f"Initialized library '{lib_id}'")
            
            if self.default_library_id is None:
                self.default_library_id = lib_id

        except Exception as e:
            logger.error(f"Failed to initialize library '{lib_id}': {e}")

    def _init_legacy_library(self, global_config: Dict):
        """Initialize a single library from the root 'tape' config section."""
        tape_config = global_config.get('tape', {})
        
        # Extract legacy config
        drive_devices = tape_config.get('drive_devices')
        drive_device = tape_config.get('drive_device') or tape_config.get('drive') or '/dev/nst0'
        changer_device = tape_config.get('changer_device') or tape_config.get('changer') or '/dev/sg1'

        device_arg = drive_device
        if isinstance(drive_devices, list) and drive_devices:
            device_arg = {i: path for i, path in enumerate(drive_devices)}

        lib_id = 'default'
        try:
            controller = TapeLibraryController(
                device=device_arg,
                changer=changer_device,
                config=global_config,
                state=load_state(),
                event_logger=self.event_logger,
                db=self.db
            )
            setattr(controller, 'library_id', lib_id)
            controller.initialize()
            
            self.controllers[lib_id] = controller
            self.default_library_id = lib_id
            logger.info("Initialized default legacy library.")
        except Exception as e:
            logger.error(f"Failed to initialize legacy library: {e}")

    def register_controller(self, lib_id: str, controller: TapeLibraryController, make_default: bool = False):
        """Register an already initialized controller (e.g. from auto-detection)."""
        setattr(controller, 'library_id', lib_id)
        self.controllers[lib_id] = controller
        if make_default or self.default_library_id is None:
            self.default_library_id = lib_id
        logger.info(f"Registered library controller '{lib_id}' (default={make_default})")

    def get_library(self, library_id: Optional[str] = None) -> Optional[TapeLibraryController]:
        """
        Get a specific library controller.
        If library_id is None, returns the default library.
        """
        if not library_id:
            return self.controllers.get(self.default_library_id)
        return self.controllers.get(library_id)

    def get_all_libraries(self) -> List[TapeLibraryController]:
        return list(self.controllers.values())

    def find_controller_for_tape(self, barcode: str) -> Optional[TapeLibraryController]:
        """Find which library contains a specific tape."""
        for lib_id, controller in self.controllers.items():
            try:
                # Use cached inventory logic from controller if available, 
                # otherwise this triggers a scan which might be slow.
                # Ideally, we should rely on DB which should track library_id.
                # For now, we scan.
                inventory = controller.inventory() 
                for tape in inventory:
                    if tape.get('barcode') == barcode:
                        return controller
            except Exception as e:
                logger.warning(f"Error checking library {lib_id} for tape {barcode}: {e}")
        return None

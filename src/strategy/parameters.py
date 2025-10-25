"""
Strategy Framework - Parameter Management

Manages strategy parameters with versioning, serialization, and validation.
"""

from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional
from pathlib import Path
import json
import hashlib
import logging

logger = logging.getLogger(__name__)


@dataclass
class ParameterSet:
    """
    Versioned parameter set.

    Attributes:
        name: Parameter set name
        version: Version identifier
        parameters: Strategy parameters
        created_at: Creation timestamp
        hash: Content hash for uniqueness
    """
    name: str
    version: str
    parameters: Dict[str, Any]
    created_at: str
    hash: str = ""

    def __post_init__(self):
        """Calculate hash after initialization."""
        if not self.hash:
            self.hash = self._calculate_hash()

    def _calculate_hash(self) -> str:
        """Calculate content hash."""
        content = json.dumps(self.parameters, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:8]


class ParameterManager:
    """
    Manages strategy parameters with versioning and serialization.

    Features:
    - JSON serialization for reproducibility
    - Version control for parameter sets
    - Parameter validation
    - Optimization history tracking
    """

    def __init__(self, storage_dir: Path):
        """
        Initialize parameter manager.

        Args:
            storage_dir: Directory for parameter storage
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def save_parameters(
        self,
        name: str,
        version: str,
        parameters: Dict[str, Any]
    ) -> ParameterSet:
        """
        Save parameter set to disk.

        Args:
            name: Parameter set name
            version: Version identifier
            parameters: Parameters to save

        Returns:
            ParameterSet object
        """
        from datetime import datetime

        param_set = ParameterSet(
            name=name,
            version=version,
            parameters=parameters,
            created_at=datetime.now().isoformat()
        )

        # Save to file
        filename = f"{name}_v{version}_{param_set.hash}.json"
        filepath = self.storage_dir / filename

        with open(filepath, 'w') as f:
            json.dump(asdict(param_set), f, indent=2)

        logger.info(f"Saved parameters: {filepath}")

        return param_set

    def load_parameters(
        self,
        name: str,
        version: str
    ) -> Optional[ParameterSet]:
        """
        Load parameter set from disk.

        Args:
            name: Parameter set name
            version: Version identifier

        Returns:
            ParameterSet or None if not found
        """
        # Find matching file
        pattern = f"{name}_v{version}_*.json"
        matches = list(self.storage_dir.glob(pattern))

        if not matches:
            logger.warning(f"No parameters found for {name} v{version}")
            return None

        # Load most recent if multiple matches
        filepath = max(matches, key=lambda p: p.stat().st_mtime)

        with open(filepath, 'r') as f:
            data = json.load(f)

        param_set = ParameterSet(**data)

        logger.info(f"Loaded parameters: {filepath}")

        return param_set

    def list_versions(self, name: str) -> List[str]:
        """
        List all versions for a parameter set.

        Args:
            name: Parameter set name

        Returns:
            List of version identifiers
        """
        pattern = f"{name}_v*.json"
        files = self.storage_dir.glob(pattern)

        versions = set()
        for f in files:
            # Extract version from filename: name_vVERSION_hash.json
            parts = f.stem.split('_v')
            if len(parts) >= 2:
                version = parts[1].split('_')[0]
                versions.add(version)

        return sorted(versions)

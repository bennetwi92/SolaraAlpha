"""
Strategy Framework - State Management

Manages strategy state persistence for recovery and replay.
"""

from dataclasses import asdict
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
from threading import Lock
import json
import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class StrategyStateEncoder(json.JSONEncoder):
    """
    Custom JSON encoder for strategy state.

    Handles common non-serializable types safely without silent string conversion.
    """

    def default(self, obj):
        """Encode non-standard types."""
        if isinstance(obj, np.ndarray):
            return {'__numpy_array__': obj.tolist()}
        elif isinstance(obj, pd.Timestamp):
            return {'__timestamp__': obj.isoformat()}
        elif isinstance(obj, datetime):
            return {'__datetime__': obj.isoformat()}
        # Don't use default=str; let it raise TypeError for unsupported types
        return super().default(obj)


def state_decoder(dct):
    """Decode strategy state objects."""
    if '__numpy_array__' in dct:
        return np.array(dct['__numpy_array__'])
    elif '__timestamp__' in dct:
        return pd.Timestamp(dct['__timestamp__'])
    elif '__datetime__' in dct:
        return datetime.fromisoformat(dct['__datetime__'])
    return dct


class StateManager:
    """
    Manages strategy state persistence for recovery.

    Features:
    - Checkpoint creation at configurable intervals
    - State serialization (JSON with proper type handling)
    - Recovery from last checkpoint
    - State replay for debugging
    - Thread-safe checkpointing

    Thread Safety:
        All checkpoint operations are protected by a lock to prevent
        corruption from concurrent access. Checkpoints only occur at
        safe points (controlled by safe_point parameter).
    """

    def __init__(
        self,
        storage_dir: Path,
        checkpoint_interval: int = 100
    ):
        """
        Initialize state manager.

        Args:
            storage_dir: Directory for state storage
            checkpoint_interval: Checkpoint frequency (number of data updates)
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_interval = checkpoint_interval
        self.update_count = 0
        self._lock = Lock()  # Thread safety

    def save_checkpoint(
        self,
        strategy: 'BaseStrategy',
        timestamp: pd.Timestamp
    ) -> Path:
        """
        Save strategy state checkpoint.

        Args:
            strategy: Strategy to checkpoint
            timestamp: Current timestamp

        Returns:
            Path to checkpoint file
        """
        # Import here to avoid circular dependency
        from src.strategy.base import Position

        checkpoint = {
            'timestamp': timestamp.isoformat(),
            'strategy_name': strategy.config.name,
            'strategy_version': strategy.config.version,
            'strategy_state': strategy.state.value,
            'parameters': strategy.config.parameters,
            'positions': {
                symbol: asdict(pos) for symbol, pos in strategy.current_positions.items()
            },
            'state_data': strategy.state_data,
            'performance': strategy.performance
        }

        # Create checkpoint filename
        checkpoint_id = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{strategy.config.name}_v{strategy.config.version}_{checkpoint_id}.json"
        filepath = self.storage_dir / filename

        # Save as JSON with proper serialization
        try:
            with open(filepath, 'w') as f:
                json.dump(checkpoint, f, indent=2, cls=StrategyStateEncoder)
        except TypeError as e:
            logger.error(f"Failed to serialize checkpoint: {e}")
            raise ValueError(
                f"Strategy state contains unserializable objects. "
                f"Only JSON-compatible types (int, float, str, list, dict) and "
                f"numpy arrays, pd.Timestamp, datetime allowed in state_data. "
                f"Error: {e}"
            )

        logger.info(f"Checkpoint saved: {filepath}")

        return filepath

    def load_checkpoint(
        self,
        strategy: 'BaseStrategy',
        checkpoint_path: Optional[Path] = None
    ) -> bool:
        """
        Load strategy state from checkpoint.

        Args:
            strategy: Strategy to restore
            checkpoint_path: Path to checkpoint (if None, loads latest)

        Returns:
            True if successful
        """
        # Import here to avoid circular dependency
        from src.strategy.base import Position, StrategyState

        if checkpoint_path is None:
            # Find latest checkpoint for this strategy
            pattern = f"{strategy.config.name}_v{strategy.config.version}_*.json"
            checkpoints = list(self.storage_dir.glob(pattern))

            if not checkpoints:
                logger.warning(f"No checkpoints found for {strategy.config.name}")
                return False

            checkpoint_path = max(checkpoints, key=lambda p: p.stat().st_mtime)

        # Load checkpoint with custom decoder
        with open(checkpoint_path, 'r') as f:
            checkpoint = json.load(f, object_hook=state_decoder)

        # Restore state
        strategy.state = StrategyState(checkpoint['strategy_state'])
        strategy.state_data = checkpoint['state_data']
        strategy.performance = checkpoint['performance']

        # Restore positions
        strategy.current_positions = {}
        for symbol, pos_dict in checkpoint['positions'].items():
            # Convert dict back to Position object
            pos_dict['entry_time'] = pd.to_datetime(pos_dict['entry_time'])
            strategy.current_positions[symbol] = Position(**pos_dict)

        logger.info(f"Checkpoint loaded: {checkpoint_path}")

        return True

    def auto_checkpoint(
        self,
        strategy: 'BaseStrategy',
        timestamp: pd.Timestamp,
        safe_point: bool = True
    ) -> Optional[Path]:
        """
        Automatically checkpoint at configured intervals with thread safety.

        Thread Safety:
            - Uses lock to prevent concurrent checkpoint operations
            - Only checkpoints at safe points (after signal generation completes)
            - Set safe_point=False during signal generation to prevent mid-operation checkpoints

        Args:
            strategy: Strategy to checkpoint
            timestamp: Current timestamp
            safe_point: Only checkpoint at safe points (default: True)

        Returns:
            Checkpoint path if created, None otherwise

        Example:
            >>> # Safe usage pattern
            >>> state_mgr.auto_checkpoint(strategy, timestamp, safe_point=False)  # During signal gen
            >>> signals = strategy.generate_signals(timestamp, data)
            >>> state_mgr.auto_checkpoint(strategy, timestamp, safe_point=True)   # After completion
        """
        # Don't checkpoint at unsafe points
        if not safe_point:
            return None

        # Thread-safe counter update
        with self._lock:
            self.update_count += 1

            if self.update_count >= self.checkpoint_interval:
                self.update_count = 0
                # Checkpoint inside lock to ensure atomicity
                return self.save_checkpoint(strategy, timestamp)

        return None

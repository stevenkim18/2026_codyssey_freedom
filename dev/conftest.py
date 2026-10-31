"""Keep property-test artifacts beneath the root .gitignore's .cache rule."""
from pathlib import Path

from hypothesis import settings
from hypothesis.database import DirectoryBasedExampleDatabase

settings.register_profile("project", database=DirectoryBasedExampleDatabase(Path(__file__).parent / ".cache" / "hypothesis"))
settings.load_profile("project")

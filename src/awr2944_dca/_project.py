import subprocess
import logging
from pathlib import Path

# Note: We will import RadarProject dynamically or inject it to avoid circular imports during transition.
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source-repo guard
# ---------------------------------------------------------------------------

def _is_source_repo(path: Path) -> bool:
    """Return True if *path* looks like the awr2944-dca package source repository.

    The check is intentionally conservative: it only fires when *both*
    conditions hold simultaneously:
    1. A ``pyproject.toml`` exists containing ``name = "awr2944-dca-lab"``
       (the exact package name in this repo's pyproject.toml).
    2. A ``src/`` subdirectory exists (standard hatch/flit source layout).

    Ordinary experiment directories will not have this combination.
    """
    ppt = path / "pyproject.toml"
    if not ppt.exists():
        return False
    if not (path / "src").is_dir():
        return False
    try:
        import tomllib  # stdlib ≥ 3.11
        with open(ppt, "rb") as f:
            data = tomllib.load(f)
        pkg_name = data.get("project", {}).get("name", "")
        # Match the exact package name — won't fire on e.g. "my-awr2944-experiment"
        return pkg_name == "awr2944-dca-lab"
    except Exception:
        return False

def _create_project(project_root: Path, name: str, git_init: bool = False):
    """Internal implementation for creating a new RadarProject."""
    from awr2944_dca.lab import RadarProject
    import uuid
    
    if project_root.exists():
        raise FileExistsError(f"Cannot create project: {project_root} already exists.")
        
    project_root.mkdir(parents=True)
    
    if git_init:
        try:
            subprocess.run(["git", "init"], cwd=project_root, check=True, capture_output=True)
        except FileNotFoundError:
            logger.warning("git not found, skipping git initialization.")
        except subprocess.CalledProcessError as e:
            logger.warning(f"git init failed: {e.stderr}")
            
    # Always write .gitignore
    gitignore_path = project_root / ".gitignore"
    with open(gitignore_path, "w") as f:
        f.write("# AWR2944 Ignore rules\n")
        f.write("# Machine-local configuration\n")
        f.write(".awr2944/local.toml\n\n")
        f.write("# Capture data\n")
        f.write("captures/\n\n")
        f.write("# Binary artifacts\n")
        f.write("*.bin\n")
        f.write("*.dat\n")
        f.write("*.mat\n")
        f.write("*.npy\n")
        f.write("*.npz\n\n")
        f.write("# Executed notebooks\n")
        f.write("*_executed.ipynb\n")
        f.write("*_output.ipynb\n\n")
        f.write("# Temporary artifacts\n")
        f.write("__pycache__/\n")
        f.write("*.pyc\n")
        f.write(".pytest_cache/\n")
            
    # Initialize configuration
    from awr2944_dca._config import ProjectConfig
    config = ProjectConfig(project_root)
    config.portable.project_name = name
    config.portable.project_id = uuid.uuid4().hex[:12]
    config.save()
    
    # Create standard directories
    (project_root / "profiles").mkdir(exist_ok=True)
    (project_root / "captures").mkdir(exist_ok=True)
    (project_root / "scripts").mkdir(exist_ok=True)
    (project_root / "notebooks").mkdir(exist_ok=True)
    
    # Write a default profile
    default_profile = project_root / "profiles" / "smoke_v1.toml"
    with open(default_profile, "w") as f:
        f.write("# Default smoke profile\n[profile]\nname = \"smoke_v1\"\n")
        
    # Write README
    readme_path = project_root / "README.md"
    with open(readme_path, "w") as f:
        f.write(f"# {name}\n\n")
        f.write("AWR2944 radar project. See `awr2944.toml` for portable configuration.\n")
        
    return RadarProject(project_root)


# ---------------------------------------------------------------------------
# Idempotent directory initializer
# ---------------------------------------------------------------------------

def _scaffold_existing_dir(
    project_root: Path,
    name: str,
    git_init: bool = False,
) -> None:
    """Apply project scaffolding to an *existing* directory, skipping any
    files/directories that are already present.

    This is the idempotent complement of :func:`_create_project` (which
    raises on existing dirs). It:
    - Never overwrites existing files.
    - Creates missing directories with ``exist_ok=True``.
    - Only creates ``awr2944.toml`` / ``local.toml`` when absent.
    - Appends missing AWR gitignore rules without duplicating existing ones.
    """
    from awr2944_dca._config import ProjectConfig
    import uuid

    # awr2944.toml — only write if absent
    portable_path = project_root / "awr2944.toml"
    if not portable_path.exists():
        config = ProjectConfig(project_root)
        config.portable.project_name = name
        config.portable.project_id = uuid.uuid4().hex[:12]
        config.save()
    # else: existing toml is authoritative — do not touch it

    # Standard directories — always idempotent
    for d in ("profiles", "captures", "scripts", "notebooks"):
        (project_root / d).mkdir(exist_ok=True)

    # .awr2944/ local config — only write if absent
    local_dir = project_root / ".awr2944"
    local_toml = local_dir / "local.toml"
    if not local_toml.exists():
        # ProjectConfig.save() already creates both files; but we only reach
        # here when awr2944.toml already existed (and save() was skipped above).
        # Create the local config independently.
        local_dir.mkdir(exist_ok=True)
        try:
            import tomli_w
            with open(local_toml, "wb") as f:
                tomli_w.dump({
                    "serial": {"com_port": "", "aux_com_port": "", "baud_rate": 115200},
                    "network": {"host_ip": ""},
                    "dca_tools": {"control_exe": "", "record_exe": "",
                                  "rf_api_dll": "", "cf_json": ""},
                }, f)
        except Exception:
            pass

    # Default smoke profile — only write if absent
    default_profile = project_root / "profiles" / "smoke_v1.toml"
    if not default_profile.exists():
        with open(default_profile, "w") as f:
            f.write("# Default smoke profile\n[profile]\nname = \"smoke_v1\"\n")

    # README — only write if absent
    readme_path = project_root / "README.md"
    if not readme_path.exists():
        with open(readme_path, "w") as f:
            f.write(f"# {name}\n\n")
            f.write("AWR2944 radar project. See `awr2944.toml` for portable configuration.\n")

    # .gitignore — append-only
    gitignore_path = project_root / ".gitignore"
    _awr_gitignore_rules = [
        "# AWR2944 Ignore rules",
        "# Machine-local configuration",
        ".awr2944/local.toml",
        "# Capture data",
        "captures/",
        "# Binary artifacts",
        "*.bin",
        "*.dat",
        "*.mat",
        "*.npy",
        "*.npz",
        "# Executed notebooks",
        "*_executed.ipynb",
        "*_output.ipynb",
        "# Temporary artifacts",
        "__pycache__/",
        "*.pyc",
        ".pytest_cache/",
    ]
    if gitignore_path.exists():
        existing = set(gitignore_path.read_text(encoding="utf-8").splitlines())
    else:
        existing = set()
    to_append = [r for r in _awr_gitignore_rules if r not in existing]
    if to_append:
        with open(gitignore_path, "a", encoding="utf-8") as f:
            if existing:
                f.write("\n")
            f.write("\n".join(to_append) + "\n")

    # Optional: git init (only if requested and repo not already present)
    if git_init and not (project_root / ".git").exists():
        try:
            subprocess.run(["git", "init"], cwd=project_root, check=True, capture_output=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            logger.warning("git init skipped: %s", e)


def init_project_at(
    path: str | Path,
    git_init: bool = False,
):
    """Idempotently initialize *path* as a RadarProject ("git init" semantics).

    - If *path* is already a valid RadarProject (has ``awr2944.toml`` or
      ``project.json``), open and return it without modification.
    - Otherwise create the standard scaffolding inside the existing directory
      without touching any pre-existing files.
    - The directory must already exist (we do not create it).

    Raises:
        FileNotFoundError: If *path* does not exist or is not a directory.
        RuntimeError: If *path* looks like the awr2944-dca package source repo.
    """
    from awr2944_dca.lab import RadarProject

    project_root = Path(path).resolve()

    if not project_root.is_dir():
        raise FileNotFoundError(
            f"Directory does not exist: {project_root}\n"
            "RadarProject.init() initializes an EXISTING directory. "
            "Use RadarProject.create() to create a new project directory."
        )

    # Source-repo guard
    if _is_source_repo(project_root):
        raise RuntimeError(
            f"{project_root} appears to be the awr2944-dca package source repository "
            "(it has pyproject.toml with name='awr2944-dca-lab' and a src/ layout). "
            "Please run init_here() from your experiment directory, not from the package source tree."
        )

    # Already initialized?
    has_toml = (project_root / "awr2944.toml").exists()
    has_json = (project_root / "project.json").exists()
    if has_toml or has_json:
        logger.debug("RadarProject.init: already initialized at %s, opening.", project_root)
        return RadarProject(project_root)

    # New initialization: use directory basename as name
    _scaffold_existing_dir(project_root, project_root.name, git_init)
    return RadarProject(project_root)


def init_project_here(git_init: bool = False):
    """Idempotently initialize the *current working directory* as a RadarProject.

    Convenience wrapper around :func:`init_project_at` for the common
    interactive case where the user is already in their experiment directory.
    """
    return init_project_at(Path.cwd(), git_init)


def create_project(name: str, parent: str | Path, git_init: bool = False):
    """
    Create a new project.
    Creates <parent>/<name>/ with full scaffolding.
    Raises FileExistsError if the directory already exists.
    """
    base_path = Path(parent).resolve()
    project_root = base_path / name
    return _create_project(project_root, name, git_init)


def create_project_at(path: str | Path, git_init: bool = False):
    """
    Create a new project at an exact path.
    The directory's basename becomes the project name.
    Raises FileExistsError if the directory already exists.
    """
    project_root = Path(path).resolve()
    return _create_project(project_root, project_root.name, git_init)


def open_project(path: str | Path):
    """
    Opens a RadarProject at the explicit path.
    """
    from awr2944_dca.lab import RadarProject
    
    project_root = Path(path).resolve()
    if not project_root.is_dir():
        raise FileNotFoundError(f"Project directory not found: {project_root}")
        
    # Check for awr2944.toml or project.json
    has_toml = (project_root / "awr2944.toml").exists()
    has_json = (project_root / "project.json").exists()
    
    if not (has_toml or has_json):
        raise FileNotFoundError(f"Not a valid RadarProject (missing awr2944.toml or project.json): {project_root}")
        
    return RadarProject(project_root)


def open_project_here():
    """
    Auto-discovers the project root starting from the current working directory.
    Walks up the directory tree looking for awr2944.toml or project.json.
    """
    from awr2944_dca.lab import RadarProject
    
    current = Path.cwd().resolve()
    while current != current.parent:
        if (current / "awr2944.toml").exists() or (current / "project.json").exists():
            return RadarProject(current)
        current = current.parent
        
    raise FileNotFoundError("Could not find a RadarProject in the current directory or any parent.")

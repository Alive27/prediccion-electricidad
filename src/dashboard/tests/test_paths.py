"""Exercise Linux container layouts on any host, without accessing the filesystem."""

import sys
import unittest
from pathlib import Path, PurePosixPath
from unittest.mock import patch

DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIRECTORY))

from streamlit.testing.v1 import AppTest  # noqa: E402

from services.data_loader import load_local  # noqa: E402
from utils.paths import OPTIONAL_PATH_VARIABLES, candidate_paths  # noqa: E402


class ContainerPath(PurePosixPath):
    """A Linux path in an environment with no mounted historical files."""

    def resolve(self):
        return self

    def expanduser(self):
        return self

    def is_file(self):
        return False

    @classmethod
    def cwd(cls):
        return cls("/app")


class ContainerPathsTests(unittest.TestCase):
    def setUp(self):
        self.path_patch = patch("utils.paths.Path", ContainerPath)
        self.env_patch = patch.dict("os.environ", {name: "" for name in OPTIONAL_PATH_VARIABLES})
        self.path_patch.start()
        self.env_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.addCleanup(self.env_patch.stop)

    def test_flat_docker_layout_has_app_and_mount_candidates(self):
        with patch("utils.paths.__file__", "/app/utils/paths.py"):
            paths = candidate_paths("demand")
        self.assertIn(ContainerPath("/app/data/raw/redata_demanda_2024.parquet"), paths)
        self.assertIn(ContainerPath("/data/raw/redata_demanda_2024.parquet"), paths)
        self.assertEqual(len(paths), len(set(paths)))

    def test_root_layout_does_not_require_any_parent(self):
        with patch("utils.paths.__file__", "/utils/paths.py"):
            paths = candidate_paths("weather")
        self.assertIn(ContainerPath("/data/raw/openmeteo_madrid_2024.parquet"), paths)

    def test_repository_layout_keeps_project_data_first(self):
        with patch("utils.paths.__file__", "/workspace/project/src/dashboard/utils/paths.py"):
            paths = candidate_paths("demand")
        self.assertEqual(paths[0], ContainerPath(
            "/workspace/project/data/raw/redata_demanda_2024.parquet",
        ))

    def test_optional_path_still_precedes_layout_candidates(self):
        with patch("utils.paths.__file__", "/app/utils/paths.py"), \
                patch.dict("os.environ", {"DASHBOARD_DATA_DIR": "/mounted/raw"}):
            paths = candidate_paths("weather")
        self.assertEqual(paths[0], ContainerPath("/mounted/raw/openmeteo_madrid_2024.parquet"))

    def test_loader_returns_unavailable_for_unmounted_container(self):
        with patch("utils.paths.__file__", "/app/utils/paths.py"):
            dataset = load_local("demand")
        self.assertFalse(dataset.available)
        self.assertFalse(dataset.errors)
        self.assertIn("/app/data/raw/redata_demanda_2024.parquet", dataset.checked_paths)

    def test_app_offers_uploads_in_unmounted_container(self):
        with patch("utils.paths.__file__", "/app/utils/paths.py"):
            app = AppTest.from_file(str(DIRECTORY / "app.py"), default_timeout=25).run()
            self.assertEqual(len(app.get("file_uploader")), 0)
            app.sidebar.button(key="nav_5").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.get("file_uploader")), 2)


if __name__ == "__main__":
    unittest.main()

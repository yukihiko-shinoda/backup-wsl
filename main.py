import os
import shutil
import stat
import time
from logging import getLogger
from logging.config import dictConfig
from pathlib import Path
from shutil import ignore_patterns
from typing import Iterable

import pywintypes
import win32con
import win32file
import yaml
from win32comext.shell import shell


class WindowsFileSystem:
    """Changes file creation time.

    - Answer: python - How do I change the file creation date of a Windows file? - Stack Overflow
    https://stackoverflow.com/a/4996407/12721873
    """

    def __init__(self):
        self.logger = getLogger(__name__)

    def create_directory(self, path_directory: Path, st_mtime: float) -> None:
        self.logger.info("Windows direcroty: %s", path_directory)
        path_directory.mkdir(parents=True, exist_ok=True)
        for count in range(3):
            try:
                self._create_directory(path_directory, st_mtime)
                return
            except PermissionError as error:
                self.logger.exception(error)
                if count >= 2:
                    raise
                time.sleep(0.1)

    def _create_directory(self, path_directory: Path, st_mtime: float) -> None:
        wintime = pywintypes.Time(st_mtime)
        winfile = win32file.CreateFile(
            str(path_directory), win32con.GENERIC_WRITE,
            win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
            None, win32con.OPEN_EXISTING,
            win32con.FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        win32file.SetFileTime(winfile, wintime, None, None)


class Copy2FromWsl:
    def __init__(self, source: str | Path, destination: str | Path):
        self.logger = getLogger(__name__)
        self.source = Path(source) if isinstance(source, str) else source
        self.destination = Path(destination) if isinstance(destination, str) else destination

    def copy2_from_wsl(self) -> str:
        if self.destination.exists() and os.stat(self.source).st_mtime == os.stat(self.destination).st_mtime:
            return str(self.destination)
        self.logger.info("Copy %s to %s", self.source, self.destination)
        # return str(self.destination)
        if self.source.is_dir():
            # Since shutil.copy2() raises error when source is directory in WSL:
            #   PermissionError: [Errno 13] Permission denied: '\\\\wsl.localhost\\Ubuntu\\root\\workspace\\tools\\zaim-csv-converter'
            WindowsFileSystem().create_directory(self.destination, os.stat(self.source).st_mtime)
            return str(self.destination)
        return shutil.copy2(self.source, self.destination)


def copy2_from_wsl(source: str | Path, destination: str | Path) -> str:
    return Copy2FromWsl(source, destination).copy2_from_wsl()


class Backup:
    EXCLUDE_DIRECTORIES = [
        ".venv",
        ".mypy_cache",
        ".tox",
        "__pycache__",
        ".ruff_cache",
        ".pytest_cache",
        ".google-drive-cache",
        ".selenium-cache",
    ]

    def __init__(self, source: Path, destination: Path) -> None:
        self.logger = getLogger(__name__)
        self.validate_source(source)
        self.validate_destination(destination)
        self.source = source
        self.destination = destination

    def validate_source(self, source: Path) -> None:
        self.logger.debug(source)
        self.check_if_exists(source)

    def validate_destination(self, destination: Path) -> None:
        for file in destination.rglob("*"):
            self.logger.debug(file)
        self.check_if_exists(destination.parent)

    def check_if_exists(self, directory: Path) -> None:
        directory.resolve()
        if not directory.exists():
            raise FileNotFoundError(f"{directory} does not exist")
        if not directory.is_dir():
            raise NotADirectoryError(f"{directory} is not a directory")

    def copytree(self) -> None:
        shutil.copytree(
            self.source,
            self.destination,
            ignore=ignore_patterns(*self.EXCLUDE_DIRECTORIES),
            copy_function=copy2_from_wsl,
            dirs_exist_ok=True
        )

    def remove_old_files(self):
        for file in (self.destination / self.source.relative_to(self.source.parent)).rglob("*"):
            source = self.source.parent / file.relative_to(self.destination)
            self.logger.debug("source: %s", source)
            if not source.exists():
                self.logger.info("Remove %s", str(file))
                file.unlink()


class WslBackupSourceDirectories:
    def __init__(self, iterable_path: Iterable[Path]):
        self.logger = getLogger(__name__)
        self.list_path = list(iterable_path)
        self.ensure_source()

    def ensure_source(self):
        for self.path in self.list_path:
            self.logger.debug("Source path: %s", self.path)
            if not self.path.exists():
                self.logger.error("%s does not exist", self.path)
                raise FileNotFoundError(f"{self.path} does not exist")
            if not self.path.is_dir():
                self.logger.error("%s is not a directory", self.path)
                raise NotADirectoryError(f"{self.path} is not a directory")

    def __iter__(self):
        return iter(self.list_path)


class WslBackupSources:
    PATH_ROOT = Path("//wsl.localhost") / "Ubuntu" / "root" / "workspace"

    def __init__(self, large_file_directory_names: list[str]):
        self.logger = getLogger(__name__)
        large = large_file_directory_names
        self.directories = WslBackupSourceDirectories(source for source in self.PATH_ROOT.glob("*") if source.name not in large)
        self.logger.debug("Source path (normal): %s", self.directories)
        self.directories_large_files = WslBackupSourceDirectories(self.PATH_ROOT / name for name in large)
        self.logger.debug("Source path (large): %s", self.directories_large_files)

    def no_longer_exists(self, file_relative: Path) -> bool:
        source = self.PATH_ROOT / file_relative
        self.logger.debug("source: %s", source)
        return not source.exists()


class WslBackupDestination:
    PATH_DIRECTORY_WSL_BACKUP = Path("wsl-backup")

    def __init__(self, path: Path):
        self.logger = getLogger(__name__)
        self.path = path / self.PATH_DIRECTORY_WSL_BACKUP
        self.ensure_destination()

    def ensure_destination(self):
        if not self.path.exists():
            self.logger.info("Create %s", self.path)
            self.path.mkdir(parents=True, exist_ok=True)

    def create_backups(self, source_directories: WslBackupSourceDirectories) -> list[Backup]:
        return [Backup(source, self.path / source.name) for source in source_directories]

    def remove_old_files(self, sources: WslBackupSources) -> None:
        for file in self.path.rglob("*"):
            file_relative = file.relative_to(self.path)
            if sources.no_longer_exists(file_relative):
                self.logger.info("Remove %s", str(file))
                if file.is_dir():
                    # Since pathlib.Path.unlink() raises error when source is directory in WSL:
                    #   PermissionError: [WinError 5] アクセスが拒否されました。: 'D:\\Users\\yukihiko-shinoda\\workspace\\wsl-backup\\amzOrderHistoryFilter'
                    shutil.rmtree(file, onexc=self.remove_readonly)
                    continue
                file.unlink()

    @staticmethod
    def remove_readonly(func, path, _):
        """Clear the readonly bit and reattempt the removal

        - shutil.rmtree で削除失敗するファイルに対する対策
        https://zenn.dev/tkm/articles/python-shutil-rmtree-delete-failure-solutions
        """
        os.chmod(path, stat.S_IWRITE)
        func(path)


class WslBackupDestinations:
    # - KNOWNFOLDERID (Knownfolders.h) - Win32 apps | Microsoft Learn
    #   https://learn.microsoft.com/ja-jp/windows/win32/shell/knownfolderid?redirectedfrom=MSDN)
    PATH_DOWNLOADS = Path(shell.SHGetKnownFolderPath("{374DE290-123F-4565-9164-39C4925E467B}"))

    def __init__(self, cloud: str, *, nas: str | None) -> None:
        self.cloud = WslBackupDestination(Path(cloud))
        self.nas = WslBackupDestination(Path(nas) if nas else self.PATH_DOWNLOADS)

    def create_backups(self, sources: WslBackupSources) -> list[Backup]:
        backups_normal = self.cloud.create_backups(sources.directories)
        backups_large = self.nas.create_backups(sources.directories_large_files)
        return backups_normal + backups_large

    def remove_old_files(self, sources: WslBackupSources) -> None:
        self.cloud.remove_old_files(sources)
        self.nas.remove_old_files(sources)


def main() -> None:
    dictConfig(yaml.safe_load(Path("logging.yml").read_text(encoding="utf-8")))
    config = yaml.safe_load(Path("config.yml").read_text(encoding="utf-8"))
    sources = WslBackupSources(config["large_file_directory_names"])
    destinations = WslBackupDestinations(config["cloud"], nas=config.get("nas"))
    for backup in destinations.create_backups(sources):
        backup.copytree()
    destinations.remove_old_files(sources)


if __name__ == "__main__":
    main()

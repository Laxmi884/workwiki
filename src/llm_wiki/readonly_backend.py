"""A filesystem backend that refuses every write: how agents see the vault and skills.

Agents read the vault through deepagents' `FilesystemBackend`; the only way anything is
written there is ingest's digest → approval → apply path, which is code, not a tool. Two
locks used to keep it that way: file tools limited to reads, and `FilesystemPermission`
rules denying writes. The rules are globs matched without dotfile support (`/**` doesn't
match `/vault/.trash/x.md`), so on their own they leave dot paths open. This class makes
read-only a property of the backend instead: whatever tool, subagent or eviction calls
`write`, `edit`, `delete` or `upload_files`, the answer is an error, for every path.
"""

from __future__ import annotations

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    FileUploadResponse,
    WriteResult,
)


def _refused(path: str) -> str:
    return f"Error: {path} is read only; agents cannot change the vault"


class ReadOnlyFilesystemBackend(FilesystemBackend):
    """`FilesystemBackend` with reads as usual and every write refused."""

    def write(self, file_path: str, content: str) -> WriteResult:  # noqa: D102
        return WriteResult(error=_refused(file_path))

    async def awrite(self, file_path: str, content: str) -> WriteResult:  # noqa: D102
        return self.write(file_path, content)

    def edit(  # noqa: D102
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return EditResult(error=_refused(file_path))

    async def aedit(  # noqa: D102
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return self.edit(file_path, old_string, new_string, replace_all)

    def delete(self, file_path: str) -> DeleteResult:  # noqa: D102
        return DeleteResult(error=_refused(file_path))

    async def adelete(self, file_path: str) -> DeleteResult:  # noqa: D102
        return self.delete(file_path)

    def upload_files(  # noqa: D102
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        return [
            FileUploadResponse(path=path, error="permission_denied")
            for path, _ in files
        ]

    async def aupload_files(  # noqa: D102
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        return self.upload_files(files)

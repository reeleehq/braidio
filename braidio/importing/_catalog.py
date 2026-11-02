"""Make an imported project's media *retrievable* — the delivery catalog.

An imported still, recording or film is recorded in the graph by its
content-addressed id, and until something registers it in the host's artifact
catalog, asking the host for it answers **404**. The writer that does that is
:mod:`nw.media_catalog` (nw#92): braidio and muvid each carried a copy of it, and
two copies of a wire contract whose row shape belongs to the host is how they
drift. Its docstring states the rules (id = content hash, blob before row,
hardlinked, never a ``file://`` url, refused on an object-store host).

This module keeps braidio's spelling of that writer — the functions the importer,
its CLI and its tests already call — and adds only what is braidio's own: the
importer's opt-out (``register_artifacts=False``) named in the backend refusal.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping, Optional

from nw.media_catalog import (
    BACKEND_ENV_KEY,
    BYTES_ROUTE,
    CATALOG_KINDS,
    DELIVERY_CATALOG_SUBPATH,
    CatalogBackendMismatch,
    CatalogReport,
    CatalogRow,
    CorruptBlob,
    CrossDeviceCatalog,
    HostArtifactCatalog,
    hash_file,
)

_REMEDY = (
    "pass register_artifacts=False to take the graph without the catalog deliberately"
)


def assert_local_backend(env: Optional[Mapping[str, str]] = None) -> None:
    """Refuse to import into a project whose host will not read its blobs.

    >>> assert_local_backend({})
    >>> assert_local_backend({"REELEE_ARTIFACT_BACKEND": "aws"})
    Traceback (most recent call last):
        ...
    nw.media_catalog.CatalogBackendMismatch: ...
    """
    from nw.media_catalog import assert_local_backend as _assert

    _assert(env, remedy=_REMEDY)


def catalog_root(project_root) -> Path:
    """``<project>/.reelee/artifacts`` — creates nothing.

    >>> catalog_root("/tmp/p").as_posix()
    '/tmp/p/.reelee/artifacts'
    """
    return HostArtifactCatalog(project_root).root


def catalog_dir(project_root) -> Path:
    """Where the rows live."""
    return HostArtifactCatalog(project_root).rows_dir


def blobs_dir(project_root) -> Path:
    """Where the content-addressed bytes live."""
    return HostArtifactCatalog(project_root).blobs_dir


def register_artifact(
    project_root,
    path,
    *,
    artifact_id: str,
    kind: str,
    generated_at: str,
    report: CatalogReport,
    width: Optional[int] = None,
    height: Optional[int] = None,
    duration_s: Optional[float] = None,
    note: str = "",
    allow_cross_device_copy: bool = False,
) -> bool:
    """Make ``artifact_id`` retrievable from ``project_root``. True if it now is.

    The importer checks the host's backend once for the whole import
    (:func:`assert_local_backend`), so this does not re-check it per file.
    """
    catalog = HostArtifactCatalog(
        project_root,
        allow_cross_device_copy=allow_cross_device_copy,
        check_backend=False,
    )
    registered = catalog.register(
        path,
        kind=kind,
        artifact_id=artifact_id,
        generated_at=generated_at,
        width=width,
        height=height,
        duration_s=duration_s,
        note=note,
        report=report,
    )
    return registered is not None


def registered_ids(project_root) -> frozenset[str]:
    """Every artifact id this project's catalog currently answers for."""
    return HostArtifactCatalog(project_root).registered_ids()


def iter_rows(project_root) -> Iterable[dict]:
    """Every catalog row, parsed. For tests and diagnostics."""
    return HostArtifactCatalog(project_root).iter_rows()


__all__ = [
    "BACKEND_ENV_KEY",
    "BYTES_ROUTE",
    "CATALOG_KINDS",
    "DELIVERY_CATALOG_SUBPATH",
    "CatalogBackendMismatch",
    "CatalogReport",
    "CatalogRow",
    "CorruptBlob",
    "CrossDeviceCatalog",
    "assert_local_backend",
    "blobs_dir",
    "catalog_dir",
    "catalog_root",
    "hash_file",
    "iter_rows",
    "register_artifact",
    "registered_ids",
]

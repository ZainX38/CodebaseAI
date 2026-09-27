from io import BytesIO
import tarfile


# Builds a .tar.gz in memory shaped like a GitHub tarball (one top folder).
# files: path --> bytes for regular files
# links: path --> target for symlinks; hardlinks: path --> target for hardlinks
def make_tarball(
    files: dict[str, bytes],
    links: dict[str, str] | None = None,
    hardlinks: dict[str, str] | None = None,
    top_folder: str | None = "ZainX38-demo-abc1234",
) -> bytes:
    buffer = BytesIO()
    prefix = f"{top_folder}/" if top_folder else ""

    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        if top_folder:
            folder = tarfile.TarInfo(top_folder)
            folder.type = tarfile.DIRTYPE
            archive.addfile(folder)

        for path, content in files.items():
            info = tarfile.TarInfo(path if path.startswith(("/", "..")) else prefix + path)
            info.size = len(content)
            archive.addfile(info, BytesIO(content))

        for link_type, entries in ((tarfile.SYMTYPE, links), (tarfile.LNKTYPE, hardlinks)):
            for path, target in (entries or {}).items():
                info = tarfile.TarInfo(prefix + path)
                info.type = link_type
                info.linkname = target
                archive.addfile(info)

    return buffer.getvalue()

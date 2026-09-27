from fastapi import HTTPException
import httpx
import pytest
import respx

from app.ingestion import archive
from app.ingestion.archive import download_tarball, extract_files, get_repository_path
from app.ingestion.snapshot import download_repository_snapshot
from tests.tarballs import make_tarball


TARBALL_URL = "https://api.github.com/repos/ZainX38/demo/tarball/abc123"
CODELOAD_URL = "https://codeload.github.com/ZainX38/demo/legacy.tar.gz/abc123"


@pytest.mark.parametrize(
    ("member_name", "expected"),
    [
        ("ZainX38-demo-abc1234/src/app.py", "src/app.py"),
        ("ZainX38-demo-abc1234/README.md", "README.md"),
        ("ZainX38-demo-abc1234", None),
        ("ZainX38-demo-abc1234/../../etc/passwd", None),
        ("ZainX38-demo-abc1234/src/./app.py", None),
        ("/etc/passwd", None),
        (r"ZainX38-demo-abc1234/src\app.py", None),
    ],
)
def test_repository_path_strips_top_folder_and_rejects_unsafe_paths(member_name, expected):
    assert get_repository_path(member_name) == expected


def test_extract_keeps_text_files_without_the_top_folder():
    extracted = extract_files(make_tarball({"src/app.py": b"print('hi')\n", "README.md": b"# Demo\n"}))

    assert extracted.files == {"src/app.py": "print('hi')\n", "README.md": "# Demo\n"}
    assert extracted.excluded == {}


def test_extract_skips_symlinks_hardlinks_and_traversal():
    tarball = make_tarball(
        {"app.py": b"x = 1\n", "../outside.py": b"evil\n"},
        links={"link.py": "/etc/passwd"},
        hardlinks={"hard.py": "ZainX38-demo-abc1234/app.py"},
    )

    extracted = extract_files(tarball)

    assert list(extracted.files) == ["app.py"]


def test_extract_records_why_files_were_left_out(monkeypatch):
    monkeypatch.setattr(archive, "MAX_FILE_BYTES", 10)

    extracted = extract_files(make_tarball({
        "small.py": b"x = 1\n",
        "big.py": b"x = 1\n" * 10,
        "latin1.txt": "café".encode("latin-1"),
    }))

    assert list(extracted.files) == ["small.py"]
    assert extracted.excluded == {"big.py": "larger than 0 KB", "latin1.txt": "not a UTF-8 text file"}


def test_extract_stops_at_the_file_limit(monkeypatch):
    monkeypatch.setattr(archive, "MAX_FILES", 1)

    extracted = extract_files(make_tarball({"a.py": b"a\n", "b.py": b"b\n"}))

    assert list(extracted.files) == ["a.py"]
    assert extracted.excluded == {"b.py": "repository file limit reached"}


def test_extract_rejects_a_broken_archive():
    with pytest.raises(HTTPException) as error:
        extract_files(b"not a tarball")

    assert error.value.status_code == 502


@respx.mock
async def test_download_follows_redirect_without_sending_the_token_to_codeload():
    tarball = make_tarball({"app.py": b"x = 1\n"})
    api = respx.get(TARBALL_URL).mock(return_value=httpx.Response(302, headers={"Location": CODELOAD_URL}))
    codeload = respx.get(CODELOAD_URL).mock(return_value=httpx.Response(200, content=tarball))

    archive_bytes = await download_tarball("gho_secret", "ZainX38", "demo", "abc123")

    assert archive_bytes == tarball
    # The token is sent to GitHub's API (needed for private repos) but not to codeload
    assert api.calls.last.request.headers["Authorization"] == "Bearer gho_secret"
    assert "Authorization" not in codeload.calls.last.request.headers


@respx.mock
async def test_download_stops_when_the_archive_is_too_big(monkeypatch):
    monkeypatch.setattr(archive, "MAX_ARCHIVE_BYTES", 5)
    sent_parts = []

    # A streamed body: if the download stops early, the later parts are never consumed
    async def archive_parts():
        for part in (b"0123", b"4567", b"89ab", b"cdef"):
            sent_parts.append(part)
            yield part

    respx.get(TARBALL_URL).mock(return_value=httpx.Response(200, content=archive_parts()))

    with pytest.raises(HTTPException) as error:
        await download_tarball("gho_secret", "ZainX38", "demo", "abc123")

    assert error.value.status_code == 413
    assert sent_parts == [b"0123", b"4567"]


@respx.mock
async def test_download_maps_github_errors():
    respx.get(TARBALL_URL).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))

    with pytest.raises(HTTPException) as error:
        await download_tarball("gho_secret", "ZainX38", "demo", "abc123")

    assert error.value.status_code == 404


def test_extract_rejects_a_truncated_archive():
    tarball = make_tarball({f"file{index}.py": f"x = {index}\n".encode() * 50 for index in range(50)})

    with pytest.raises(HTTPException) as error:
        extract_files(tarball[: len(tarball) // 2])

    assert error.value.status_code == 502


def test_extract_stops_an_archive_with_too_many_entries(monkeypatch):
    monkeypatch.setattr(archive, "MAX_ARCHIVE_ENTRIES", 10)
    # Excluded entries count too, otherwise a "zip bomb" of node_modules files is free
    tarball = make_tarball({f"node_modules/pkg{index}/index.js": b"" for index in range(20)})

    with pytest.raises(HTTPException) as error:
        extract_files(tarball)

    assert error.value.status_code == 413


def test_extract_stops_when_the_uncompressed_size_is_too_big(monkeypatch):
    monkeypatch.setattr(archive, "MAX_UNCOMPRESSED_BYTES", 1000)

    with pytest.raises(HTTPException) as error:
        extract_files(make_tarball({"big.py": b"0" * 2000}))

    assert error.value.status_code == 413


def test_extract_stops_at_the_total_source_size(monkeypatch):
    monkeypatch.setattr(archive, "MAX_SOURCE_BYTES", 10)

    extracted = extract_files(make_tarball({"a.py": b"a = 1\n", "b.py": b"b = 22\n"}))

    assert list(extracted.files) == ["a.py"]
    assert extracted.excluded == {"b.py": "repository size limit reached"}


def test_extract_records_links():
    extracted = extract_files(make_tarball({"app.py": b"x = 1\n"}, links={"link.py": "app.py"}))

    assert extracted.excluded == {"link.py": "link, not a regular file"}


def test_extract_ignores_entries_outside_the_first_top_folder():
    tarball = make_tarball({"repo-a/x.py": b"x = 1\n", "repo-b/x.py": b"x = 2\n"}, top_folder=None)

    assert extract_files(tarball).files == {"x.py": "x = 1\n"}


@respx.mock
@pytest.mark.parametrize(
    ("status_code", "detail"),
    [(404, "Branch not found"), (409, "Repository is empty")],
)
async def test_latest_commit_errors(status_code, detail):
    from app.github.repositories import get_latest_commit_sha

    respx.get("https://api.github.com/repos/ZainX38/demo/commits/main").mock(
        return_value=httpx.Response(status_code)
    )

    with pytest.raises(HTTPException) as error:
        await get_latest_commit_sha("gho_secret", "ZainX38", "demo", "main")

    assert (error.value.status_code, error.value.detail) == (status_code, detail)


@respx.mock
async def test_snapshot_is_pinned_to_the_latest_commit_of_the_default_branch():
    respx.get("https://api.github.com/repos/ZainX38/demo").mock(return_value=httpx.Response(200, json={
        "owner": {"login": "ZainX38"}, "name": "demo", "default_branch": "main", "private": True,
    }))
    respx.get("https://api.github.com/repos/ZainX38/demo/commits/main").mock(
        return_value=httpx.Response(200, json={"sha": "abc123"})
    )
    respx.get(TARBALL_URL).mock(return_value=httpx.Response(200, content=make_tarball({"app.py": b"x = 1\n"})))

    snapshot = await download_repository_snapshot("gho_secret", "ZainX38", "demo")

    assert (snapshot.default_branch, snapshot.commit_sha) == ("main", "abc123")
    assert snapshot.files == {"app.py": "x = 1\n"}


@respx.mock
async def test_snapshot_refuses_a_repository_the_user_cannot_access():
    respx.get("https://api.github.com/repos/ZainX38/secret").mock(return_value=httpx.Response(404))

    with pytest.raises(HTTPException) as error:
        await download_repository_snapshot("gho_secret", "ZainX38", "secret")

    assert error.value.status_code == 404
    assert error.value.detail == "Repository not found or you don't have access to it"

from urllib.parse import quote


def _get_github_repository_base_url(owner: str, repo_name: str) -> str:
    encoded_owner = quote(owner, safe="")
    encoded_repo_name = quote(repo_name, safe="")
    return f"https://api.github.com/repos/{encoded_owner}/{encoded_repo_name}"


# Synchronous operation as it is only string manipulation and no network requests to wait
def get_github_contents_url(owner: str, repo_name: str, path: str) -> str:
    encoded_path = "/".join(quote(path_part, safe="") for path_part in path.split("/") if path_part)
    github_api_url = f"{_get_github_repository_base_url(owner, repo_name)}/contents"

    if encoded_path:
        github_api_url = f"{github_api_url}/{encoded_path}"

    return github_api_url


def get_github_repositories_url() -> str:
    return "https://api.github.com/user/repos"


# Returns the repository metadata, e.g. its default branch
def get_github_repository_url(owner: str, repo_name: str) -> str:
    return _get_github_repository_base_url(owner, repo_name)


# ref can be a branch name or a commit SHA. Branch names can contain "/" so it is fully encoded
def get_github_commit_url(owner: str, repo_name: str, ref: str) -> str:
    return f"{_get_github_repository_base_url(owner, repo_name)}/commits/{quote(ref, safe='')}"


# The whole repository at one exact commit, as a single .tar.gz download
def get_github_tarball_url(owner: str, repo_name: str, commit_sha: str) -> str:
    return f"{_get_github_repository_base_url(owner, repo_name)}/tarball/{quote(commit_sha, safe='')}"

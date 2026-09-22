from urllib.parse import quote


# Synchronous operation as it is only string manipulation and no network requests to wait
def get_github_contents_url(owner: str, repo_name: str, path: str) -> str:
    encoded_owner = quote(owner, safe="")
    encoded_repo_name = quote(repo_name, safe="")
    encoded_path = "/".join(quote(path_part, safe="") for path_part in path.split("/") if path_part)
    github_api_url = f"https://api.github.com/repos/{encoded_owner}/{encoded_repo_name}/contents"

    if encoded_path:
        github_api_url = f"{github_api_url}/{encoded_path}"

    return github_api_url


def get_github_repositories_url() -> str:
    return "https://api.github.com/user/repos"

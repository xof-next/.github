import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
 
ORG = os.environ.get("ORG", "xof-next") 
TOKEN = os.environ.get("GITHUB_TOKEN", "")
README_PATH = os.environ.get("README_PATH", "README.md")
 
API = "https://api.github.com"
START = "<!-- STATS:START -->"
END = "<!-- STATS:END -->" 

BAR_WIDTH = 24
TOP_REPOS = 10
TOP_LANGUAGES = 8
TOP_CONTRIBUTORS = 10
RECENT_REPOS = 200
STAR_HISTORY_REPOS = 200


def request_json(url):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "xof-org-stats",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        if error.code in (404, 409):
            return None
        raise
    return json.loads(body) if body else None


def get_all(path):
    items = []
    page = 1
    separator = "&" if "?" in path else "?"
    while True:
        data = request_json(f"{API}{path}{separator}per_page=100&page={page}")
        if not data:
            break
        items.extend(data)
        if len(data) < 100:
            break
        page += 1
    return items


def collect():
    """Gather repositories, language byte counts and commit counts per login."""
    repos = [repo for repo in get_all(f"/orgs/{ORG}/repos?type=public") if not repo.get("fork")]
    languages = {}
    contributors = {}
    for repo in repos:
        name = repo["name"]
        for language, size in (request_json(f"{API}/repos/{ORG}/{name}/languages") or {}).items():
            languages[language] = languages.get(language, 0) + size
        try:
            people = get_all(f"/repos/{ORG}/{name}/contributors")
        except urllib.error.HTTPError as error:
            print(f"warning: contributors unavailable for {name} (HTTP {error.code})", file=sys.stderr)
            people = []
        for person in people:
            if person.get("type") == "Bot":
                continue
            login = person["login"]
            contributors[login] = contributors.get(login, 0) + person.get("contributions", 0)
    return repos, languages, contributors


def bar(percent):
    filled = round(percent / 100 * BAR_WIDTH)
    if percent > 0:
        filled = max(filled, 1)
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def day(timestamp):
    """Return the YYYY-MM-DD part of an ISO timestamp, or a dash."""
    return timestamp[:10] if timestamp else "—"


def cell(text):
    """Escape text so it can sit inside a markdown table cell."""
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def render(repos, languages, contributors, now=None):
    now = now or datetime.now(timezone.utc)
    by_stars = sorted(repos, key=lambda r: (-r.get("stargazers_count", 0), r["name"].lower()))
    total_stars = sum(r.get("stargazers_count", 0) for r in repos)
    total_forks = sum(r.get("forks_count", 0) for r in repos)
    lines = [
        f"_Last updated {now:%Y-%m-%d %H:%M} UTC · generated automatically by GitHub Actions_",
        "",
        "| Repositories | Stars | Forks | Contributors | Languages |",
        "|:---:|:---:|:---:|:---:|:---:|",
        f"| {len(repos)} | {total_stars} | {total_forks} | {len(contributors)} | {len(languages)} |",
        "",
        "### Top repositories",
        "",
        "| # | Repository | Description | Stars | Forks | Language | Last push |",
        "|:---:|:---|:---|:---:|:---:|:---:|:---:|",
    ]
    for index, repo in enumerate(by_stars[:TOP_REPOS], 1):
        lines.append(
            f"| {index} | [`{cell(repo['name'])}`]({repo['html_url']}) "
            f"| {cell(repo.get('description') or '—')} "
            f"| {repo.get('stargazers_count', 0)} | {repo.get('forks_count', 0)} "
            f"| {cell(repo.get('language') or '—')} | {day(repo.get('pushed_at'))} |"
        )

    lines += ["", "### Top languages", ""]
    total_bytes = sum(languages.values())
    if total_bytes:
        ranked = sorted(languages.items(), key=lambda kv: (-kv[1], kv[0].lower()))
        lines.append("```text")
        for language, size in ranked[:TOP_LANGUAGES]:
            percent = size / total_bytes * 100
            lines.append(f"{language:<14} {bar(percent)} {percent:5.1f}%")
        lines.append("```")
    else:
        lines.append("_No language data yet._")

    lines += ["", "### Top contributors", ""]
    ranked_people = sorted(contributors.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    ranked_people = ranked_people[:TOP_CONTRIBUTORS]
    if ranked_people:
        avatars = "".join(
            f'<a href="https://github.com/{login}" title="{login}: {commits} commits">'
            f'<img src="https://github.com/{login}.png?size=96" width="56" height="56" alt="{login}"></a>'
            for login, commits in ranked_people
        )
        lines.append(f'<p align="center">{avatars}</p>')
        lines += ["", "| # | Contributor | Commits |", "|:---:|:---|:---:|"]
        for index, (login, commits) in enumerate(ranked_people, 1):
            lines.append(f"| {index} | [@{login}](https://github.com/{login}) | {commits} |")
    else:
        lines.append("_No contributor data yet._")

    lines += ["", "### Recently updated", ""]
    recent = sorted(repos, key=lambda r: r.get("pushed_at") or "", reverse=True)[:RECENT_REPOS]
    for repo in recent:
        lines.append(
            f"- [`{repo['name']}`]({repo['html_url']}) · last push {day(repo.get('pushed_at'))}"
        )
    if not recent:
        lines.append("_No public repositories yet._")

    starred = [r["full_name"] for r in by_stars if r.get("stargazers_count", 0) > 0]
    starred = starred[:STAR_HISTORY_REPOS]
    if starred:
        repo_list = ",".join(starred)
        lines += [
            "",
            "### Star history",
            "",
            f'<p align="center"><img src="https://api.star-history.com/svg?repos={repo_list}&amp;type=Date" '
            'alt="Star history"></p>',
        ]
    return "\n".join(lines)


def update_readme(block, path=None):
    path = path or README_PATH
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)
    if not pattern.search(text):
        sys.exit(f"error: {START} and {END} markers not found in {path}")
    updated = pattern.sub(lambda _match: f"{START}\n{block}\n{END}", text, count=1)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(updated)
    print(f"updated {path}")


def main():
    repos, languages, contributors = collect()
    update_readme(render(repos, languages, contributors))


if __name__ == "__main__":
    main()

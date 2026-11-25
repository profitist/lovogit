from urllib.parse import quote


def verify_location(location, snapshot, limits):
    result = {**location, "verified": False, "snippet": "", "permalink": None}
    try:
        relative = location["file"]
        line = location.get("line", 1)
        root = snapshot.root.resolve()
        path = root / relative
        if path.is_absolute() and not path.is_relative_to(root):
            raise ValueError("outside_snapshot")
        if not path.resolve().is_relative_to(root):
            raise ValueError("outside_snapshot")
        if any(
            parent.is_symlink()
            for parent in (path, *path.parents)
            if parent != root and parent.is_relative_to(root)
        ):
            raise ValueError("symlink")
        if not path.is_file():
            raise ValueError("missing_file")
        if type(line) is not int or line < 1:
            raise ValueError("invalid_line")
        start = max(1, line - limits.UPDATE_SNIPPET_LINES // 2)
        lines, found = [], False
        with path.open(encoding="utf-8") as stream:
            for number, text in enumerate(stream, 1):
                if number == line:
                    found = True
                if start <= number < start + limits.UPDATE_SNIPPET_LINES:
                    lines.append(text[: limits.UPDATE_SNIPPET_CHARS])
                if number >= start + limits.UPDATE_SNIPPET_LINES:
                    break
        if not found:
            raise ValueError("invalid_line")
        relative = path.relative_to(root).as_posix()
        snippet = "".join(lines)
        result.update(
            verified=True,
            file=relative,
            line=line,
            snippet=snippet[: limits.UPDATE_SNIPPET_CHARS],
            snippet_start_line=start,
            truncated=len(snippet) > limits.UPDATE_SNIPPET_CHARS,
            permalink=f"{snapshot.repo.repo_url}/blob/{snapshot.repo.commit_sha}/{quote(relative)}#L{line}",
        )
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as error:
        result["diagnostic"] = (
            str(error) if isinstance(error, ValueError) else type(error).__name__
        )
    return result

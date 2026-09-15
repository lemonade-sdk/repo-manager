"""`repo-manager site ...` — the dashboard, rendered or served from the state directory."""

from repo_manager import site
from repo_manager.context import Context


def add_parser(sub, shared):
    parser = sub.add_parser("site", help="Render or serve the dashboard.")
    nouns = parser.add_subparsers(dest="verb", required=True)

    render = nouns.add_parser(
        "render", parents=[shared], help="Write the static site. No network, no checkout."
    )
    render.add_argument("--out", required=True, help="Directory to write the site into.")
    render.set_defaults(func=cmd_render)

    serve = nouns.add_parser("serve", parents=[shared], help="Serve the dashboard locally.")
    serve.add_argument("--host", default="127.0.0.1",
                       help="Address to bind. 0.0.0.0 serves the UI to the LAN.")
    serve.add_argument("--port", type=int, default=8765, help="Port to bind (default: 8765).")
    serve.add_argument("--no-open", action="store_true", help="Print the URL without opening a browser.")
    serve.set_defaults(func=cmd_serve)


def cmd_render(args):
    ctx = Context(args)
    index = site.render(ctx.store, args.out)
    counts = site.load(ctx.store)["counts"]
    print(
        f"Wrote {index}: {counts['release_reviews']} release review(s), "
        f"{counts['commits']} commit review(s), {counts['pr_reviews']} PR triage(s)"
    )
    return 0


def cmd_serve(args):
    ctx = Context(args)
    site.serve(ctx, args.host, args.port, open_browser=not args.no_open)
    return 0

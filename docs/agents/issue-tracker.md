# Issue tracker: GitHub

Use GitHub Issues in ray-su/ha-skylink-orbit. Use the installed `gh` CLI.
Verify `gh repo view --json nameWithOwner` before creating issues.
Read issues with `gh issue view NUMBER --json number,title,body,labels,comments`.
Create issues with `gh issue create --title TITLE --body-file PATH`; preserve real newlines in the body file.
Track dependencies using GitHub issue dependencies; if unavailable, record `Blocked by: #NUMBER` in the dependent issue.
When a skill says to publish to the issue tracker, create a GitHub issue.
When a skill says to fetch a ticket, read the corresponding issue and comments.
PRs as a request surface: no.

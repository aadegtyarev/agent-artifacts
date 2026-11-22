# Projects: design

**Partly built.** This is the approved design for the parts of projects that
are not built yet. What is shipped is described in [the
contract](../contract.md): a file's `title`, `project`, `section` and
`updated_at`, the project calls under `/api/v1/projects`, lifetimes, limits,
storage and the CLI commands (see [Projects](../contract.md#projects)), and
the `/p/{id}` page (see [The project page](../contract.md#the-project-page)).
Not built: the project's file list in the viewer and links between files.
When a part is built, it moves into the contract.

## Scenario

A job grows into many files: contracts, app mockups, site mockups. The
agent puts them in a project, each with a title and a section. The person
keeps one link to the project page, sees the files grouped by section,
moves between them from any file's header, and follows links the agent
put inside the files. Project files stay until deleted; when the agent's
space runs out, it deletes superseded files itself and says what it removed.

## Projects

A file in a project is as open as the project: its viewer page shows the
project's file list, so anyone with the link to one file sees the titles of
the project's other files and can open them. Moving a file into a project
publishes it to everyone who has a link to that project or to any of its
files.

- Links to a deleted file or project lead to the `404` page.

**The project's file list.** The viewer page of a file in a project shows
the project's title in the header, and "kept until deleted" instead of the
expiry (built). A tap on the title opens the file list, apart from the "≡"
menu: the same list as the project page, the current file marked, and a link
to the project page; it is rendered on the server. Moving between files
happens in the same tab.

**Links between files.** In HTML and Markdown, a plain left click on a link
whose target, resolved against the document's own URL, has the same origin
and the path `/a/{id}` or `/p/{id}` opens that page in the viewer's own tab
instead of inside the frame. The pin script (see [Comments](../contract.md#comments))
catches such a click, sends the viewer the path and fragment, and cancels the
click; a link the viewer then refuses does nothing. The viewer:

- accepts it only while the page has a user activation from that click
  (`navigator.userActivation.isActive`), so the content cannot move the tab
  on its own;
- matches the path in full against `^/(a|p)/[A-Za-z0-9_-]{22}$` and the
  fragment against `^[A-Za-z0-9_.:-]{0,64}$`, and builds the new URL from
  those parts on its own origin; anything else it ignores;
- on arrival, opens the content at that fragment; a Markdown page scrolls to
  it after rendering.

At worst, content can send the person to another page of the same service
on a click inside it. Comment
mode takes precedence: while it is on, a tap places a pin and follows no
link. Links with a modifier key or `target="_blank"`, links to other sites,
and links to `/raw`, `/download` or with a query string behave as before.
Text files have no links, and SVG gets no script. Without the script such
links work only with `target="_blank"`.

## Security

A project page lists only its own files. A file in a project shows its
project, see [Projects](#projects).

## Skill

Once these are built, the skill also teaches:

- links to other files of the service as `/a/{id}` or `/p/{id}`;
- that a project file shows the whole project to whoever has its link.

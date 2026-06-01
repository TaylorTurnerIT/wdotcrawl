import codecs
import json
import os
import pickle
import subprocess

import wikidot

# Repository builder and maintainer
# Contains logic for actual loading and maintaining the repository over the course of its construction.

# Usage:
#   rm = RepoMaintainer(wikidot, path)
#   rm.buildRevisionList(pages, depth)
#   rm.openRepo()
#   while rm.commitNext():
# pass
#   rm.cleanup()

# Talkative.


class RepoMaintainer:
    def __init__(self, wikidot, path):
        # Settings
        self.wd = wikidot  # Wikidot instance
        self.path = path  # Path to repository
        self.debug = False  # = True to enable more printing
        self.storeRevIds = True  # = True to store .revid with each commit

        # Internal state
        self.wrevs = None  # Compiled wikidot revision list (history)

        self.rev_no = 0  # Next revision to process
        self.last_names = {}  # Tracks page renames: name atm -> last name in repo
        self.last_parents = {}  # Tracks page parent names: name atm -> last parent in repo

    def _git(self, *args, extra_env=None):
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        result = subprocess.run(
            ["git"] + list(args), cwd=self.path, capture_output=True, text=True, env=env
        )
        if result.returncode != 0:
            raise Exception("git error: " + result.stderr)
        return result.stdout

    def _wrevs_path(self):
        return os.path.join(self.path, ".wrevs")

    def _wstate_path(self):
        return os.path.join(self.path, ".wstate")

    def _page_ids_path(self):
        return os.path.join(self.path, "page_ids.json")

    #
    # Saves and loads revision list from file
    #
    def saveWRevs(self):
        with open(self._wrevs_path(), "wb") as fp:
            pickle.dump(self.wrevs, fp)

    def loadWRevs(self):
        with open(self._wrevs_path(), "rb") as fp:
            self.wrevs = pickle.load(fp)

    #
    # Page ID cache — avoids an HTTP GET per page on every incremental run.
    #
    def _load_page_ids(self):
        path = self._page_ids_path()
        if os.path.isfile(path):
            with open(path, "r") as f:
                return json.load(f)
        return {}

    def _save_page_ids(self, ids):
        path = self._page_ids_path()
        with open(path, "w") as f:
            json.dump(ids, f, indent=2, sort_keys=True)

    def _get_page_id_cached(self, page_name):
        """Get page ID from cache, fetching from Wikidot only if missing."""
        cache = self._load_page_ids()
        if page_name in cache:
            return cache[page_name]
        page_id = self.wd.get_page_id(page_name)
        if page_id:
            cache[page_name] = page_id
            self._save_page_ids(cache)
        return page_id

    #
    # Compiles a combined revision list for a given set of pages, or all pages on the site.
    #  pages: compile history for these pages
    #  depth: download at most this number of revisions.
    #
    # If there exists a cached revision list at the repository destination,
    # it is loaded and no requests are made.
    #
    def buildRevisionList(self, pages=None, depth=10000, since_time=0):
        # For incremental runs skip the cache — it belongs to a prior full dump
        if since_time == 0 and os.path.isfile(self._wrevs_path()):
            print("Loading cached revision list...")
            self.loadWRevs()
        else:
            if since_time > 0:
                print(
                    "Building incremental revision list (since {})...".format(
                        since_time
                    )
                )
            else:
                print("Building revision list...")
            if not pages:
                if since_time > 0:
                    print("Attempting to fetch sitemap.xml for optimization...")
                    sitemap_pages = self.wd.get_pages_from_sitemap(since_time)
                    if sitemap_pages is not None:
                        pages = sitemap_pages
                        print("Found {} changed pages from sitemap.".format(len(pages)))
                    else:
                        print("Sitemap unavailable, falling back to full page listing.")
                        pages = self.wd.list_pages(10000)
                else:
                    pages = self.wd.list_pages(10000)
            self.wrevs = []
            pages_with_changes = 0
            pages_without_changes = 0
            for page in pages:
                print("Querying page: " + page)
                page_id = self._get_page_id_cached(page)
                print("ID: " + str(page_id))
                revs = self.wd.get_revisions(page_id, depth)
                new_revs = [r for r in revs if r["date"] > since_time]
                if new_revs:
                    pages_with_changes += 1
                else:
                    pages_without_changes += 1
                print(
                    "Revisions: "
                    + str(len(new_revs))
                    + " new ("
                    + str(len(revs))
                    + " total)"
                )
                for rev in new_revs:
                    self.wrevs.append(
                        {
                            "page_id": page_id,
                            "page_name": page,  # name atm, not at revision time
                            "rev_id": rev["id"],
                            "date": rev["date"],
                            "user": rev["user"],
                            "comment": rev["comment"],
                        }
                    )
            self.saveWRevs()  # Save a cached copy
            print("")
            if since_time > 0:
                print(
                    "Pages with changes: {}, unchanged: {} (total queried: {})".format(
                        pages_with_changes, pages_without_changes, len(pages)
                    )
                )

        print("Total revisions to process: " + str(len(self.wrevs)))

        print("Sorting revisions...")
        self.wrevs.sort(key=lambda rev: rev["date"])
        print("")

        if self.debug:
            print("Revision list: ")
            for rev in self.wrevs:
                print(str(rev) + "\n")
            print("")

    #
    # Saves and loads operational state from file
    #
    def saveState(self):
        # Write to a temp file then atomically rename so a crash mid-write
        # never leaves a partial/corrupt .wstate.
        tmp = self._wstate_path() + ".tmp"
        with open(tmp, "wb") as fp:
            pickle.dump(self.rev_no, fp)
            pickle.dump(self.last_names, fp)
            pickle.dump(self.last_parents, fp)
        os.replace(tmp, self._wstate_path())

    def loadState(self):
        with open(self._wstate_path(), "rb") as fp:
            self.rev_no = pickle.load(fp)
            self.last_names = pickle.load(fp)
            try:
                self.last_parents = pickle.load(fp)
            except EOFError:
                pass

    #
    # Initializes the construction process, after the revision list has been compiled.
    # Either creates a new repo, or loads the existing one at the target path
    # and restores its construction state.
    #
    def openRepo(self):
        self.last_names = {}  # Tracks page renames: name atm -> last name in repo
        self.last_parents = {}  # Tracks page parent names: name atm -> last parent in repo

        repo_exists = os.path.isdir(os.path.join(self.path, ".git"))

        # Clean up any leftover temp file from a previous crash during saveState
        tmp = self._wstate_path() + ".tmp"
        if os.path.isfile(tmp):
            os.remove(tmp)

        if os.path.isfile(self._wstate_path()):
            print("Continuing from aborted dump state...")
            self.loadState()

        elif repo_exists:
            # Incremental update: populate last_names from existing .txt files
            # so renames against already-committed pages are detected correctly
            print("Updating existing repository...")
            self.rev_no = 0
            pages_dir = os.path.join(self.path, "pages")
            if os.path.isdir(pages_dir):
                for entry in os.listdir(pages_dir):
                    if entry.endswith(".txt"):
                        name = entry[:-4]
                        self.last_names[name] = name

        else:
            print("Initializing repository...")
            self._git("init")
            self._git("checkout", "-b", "main")
            self.rev_no = 0

            if self.storeRevIds:
                # Add revision id file to track per-commit wikidot rev id
                fname = os.path.join(self.path, ".revid")
                codecs.open(fname, "w", "UTF-8").close()
                self._git("add", ".revid")

    #
    # Takes an unprocessed revision from a revision log, fetches its data and commits it.
    # Returns false if no unprocessed revisions remain.
    #
    def commitNext(self):
        if self.rev_no >= len(self.wrevs):
            return False

        rev = self.wrevs[self.rev_no]
        source = self.wd.get_revision_source(rev["rev_id"])
        # Page title and unix_name changes are only available through another request:
        details = self.wd.get_revision_version(rev["rev_id"])

        # Store revision_id for last commit so empty commits (e.g. file uploads) still produce a change.
        revid_fname = os.path.join(self.path, ".revid")
        if self.storeRevIds:
            with codecs.open(revid_fname, "w", "UTF-8") as outp:
                outp.write(rev["rev_id"])

        unixname = rev["page_name"]
        rev_unixname = details["unixname"]  # may be different in revision than atm

        # Unfortunately, there's no exposed way in Wikidot to see page breadcrumbs at any point in history.
        # The only way to know they were changed is revision comments, though evil people may trick us.
        if rev["comment"].startswith('Parent page set to: "'):
            # This is a parenting revision, remember the new parent
            parent_unixname = rev["comment"][21:-2]
            self.last_parents[unixname] = parent_unixname
        else:
            # Else use last parent_unixname we've recorded
            parent_unixname = (
                self.last_parents[unixname] if unixname in self.last_parents else None
            )
        # There are also problems when parent page gets renamed -- see updateChildren

        # If the page is tracked and its name just changed, tell git
        rename = (unixname in self.last_names) and (
            self.last_names[unixname] != rev_unixname
        )
        if rename:
            old_path = os.path.join(
                self.path, "pages", str(self.last_names[unixname]) + ".txt"
            )
            if os.path.isfile(old_path):
                self.updateChildren(self.last_names[unixname], rev_unixname)
                self._git(
                    "mv",
                    "pages/" + str(self.last_names[unixname]) + ".txt",
                    "pages/" + str(rev_unixname) + ".txt",
                )
            else:
                print(
                    "  [WARN] Rename source missing, writing fresh: pages/{}.txt -> pages/{}.txt".format(
                        self.last_names[unixname], rev_unixname
                    )
                )

        # Output contents
        fname = os.path.join(self.path, "pages", rev_unixname + ".txt")
        os.makedirs(os.path.dirname(fname), exist_ok=True)
        with codecs.open(fname, "w", "UTF-8") as outp:
            if details["title"]:
                outp.write("title:" + details["title"] + "\n")
            if parent_unixname:
                outp.write("parent:" + parent_unixname + "\n")
            outp.write(source)

        # Stage the page file (new or modified) and .revid
        self._git("add", "pages/" + rev_unixname + ".txt")
        if self.storeRevIds:
            self._git("add", ".revid")

        self.last_names[unixname] = rev_unixname

        # Commit
        if rev["comment"] != "":
            commit_msg = rev_unixname + ": " + rev["comment"]
        else:
            commit_msg = rev_unixname

        user = rev["user"] or "unknown"
        author = "{} <{}@wikidot.invalid>".format(user, user)

        print("Commiting: " + str(self.rev_no) + ". " + commit_msg)

        # Set both author and committer date via env so history timestamps are accurate
        extra_env = {}
        if rev["date"]:
            ts = "@{}".format(rev["date"])
            extra_env["GIT_AUTHOR_DATE"] = ts
            extra_env["GIT_COMMITTER_DATE"] = ts

        self._git(
            "commit",
            "--allow-empty",
            "-m",
            commit_msg,
            "--author",
            author,
            extra_env=extra_env,
        )

        self.rev_no += 1

        self.saveState()  # Update operation state
        return True

    #
    # Updates all children of the page to reflect parent's unixname change.
    #
    def updateChildren(self, oldunixname, newunixname):
        for child in self.last_parents.keys():
            if self.last_parents[child] == oldunixname:
                self.updateParentField(child, self.last_parents[child], newunixname)

    #
    # Processes a page file and updates "parent:..." string to reflect a change in parent's unixname.
    #
    def updateParentField(self, child_unixname, parent_oldunixname, parent_newunixname):
        child_path = os.path.join(self.path, "pages", child_unixname + ".txt")
        with codecs.open(child_path, "r", "UTF-8") as f:
            content = f.readlines()
        idx = content.index("parent:" + parent_oldunixname + "\n")
        if idx < 0:
            raise Exception(
                "Cannot update child page "
                + child_unixname
                + ": "
                + "it is expected to have parent set to "
                + parent_oldunixname
                + ", but there seems to be no such record in it."
            )
        content[idx] = "parent:" + parent_newunixname + "\n"
        with codecs.open(child_path, "w", "UTF-8") as f:
            f.writelines(content)

    #
    # Finalizes the construction process and deletes any temporary files.
    #
    def cleanup(self):
        os.remove(self._wstate_path())
        os.remove(self._wrevs_path())

import argparse
import logging
import os
import os.path
import _git as git
import _p4 as p4


logger = logging.getLogger(__name__)


description = """
This utility converts commits from a Git repository into changelists in
a P4 depot. Both need to be in the same place, i.e. sharing the same
physical files on disk.
"""


def main():
    # Create argument parser.
    parser = argparse.ArgumentParser("Git->P4")
    parser.description = description
    parser.add_argument(
        "range",
        nargs='?',
        help="The git commit, or range of git commits, to convert (defaults to p4..master)")

    # Convenience flags.
    parser.add_argument(
        '-p', '--p4-work',
        action="store_true",
        help=("Stay with P4 to continue working, instead of setting everything "
              "back up to continue working in git (which is the default). "
              "This is a convenience flag that enables the following flags: \n"
              "--no-revert --ignore-opened --stay"))

    # Advanced flags.
    parser.add_argument(
        '--no-revert',
        action="store_true",
        help="Don't revert P4 changelists after they're shelved")
    parser.add_argument(
        "--no-shelve",
        action="store_true",
        help="Don't shelve P4 changelists after they're created (this implies --no-revert)")
    parser.add_argument(
        "--ignore-opened",
        action="store_true",
        help="Ignore already opened files in P4")
    parser.add_argument(
        "--stay",
        action="store_true",
        help="Don't return to originally checked out commit, stay on the latest converted one")
    parser.add_argument(
        "--no-p4-branch",
        action="store_true",
        help="Don't auto-manage the p4 branch head")
    parser.add_argument(
        "--rename-threshold", type=int,
        help="Specify a threshold for git to detect file renames")

    # Troubleshooting flags.
    parser.add_argument(
        "-n", "--dry-run",
        action="store_true",
        help="Don't actually do anything")
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug messages")

    args = parser.parse_args()

    # Setup convenience flags.
    if args.p4_work:
        args.no_revert = True
        args.ignore_opened = True
        args.stay = True

    # Setup logging.
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    # Check that we don't have local changes in git that we would lose as we checkout the
    # commits we want to convert.
    status = git.run_command(["status", "--porcelain"])
    if status:
        logger.warning("Git repository has local changes!")
        return 1

    # Remember what commit we are on.
    current_head = git.run_command(["rev-parse", "HEAD"]).strip()
    current_branch = git.run_command(["branch", "--show-current"]).strip()

    # Look for the p4 branch head.
    try:
        p4_hash = git.run_command(["rev-parse", "p4"]).strip()
        logger.debug("P4 branch is currently at [%s]" % p4_hash)
    except git.GitError:
        logger.warning("No P4 branch found, will start from HEAD")
        p4_hash = 'HEAD'

    # Get the default branch and see where it's at.
    git_default_branch = git.run_command(['var', 'GIT_DEFAULT_BRANCH']).strip()
    master_hash = git.run_command(['rev-parse', git_default_branch]).strip()
    logger.debug("Git default branch is '%s', currently at [%s]" % (git_default_branch, master_hash))

    # Figure out the range of commits to convert.
    # Either a given range, or everything since p4 up to HEAD or to the given
    # commit hash.
    if args.range and ".." in args.range:
        hash_range = args.range
    else:
        hash_range = "%s..%s" % (p4_hash, (args.range or git_default_branch))
    commit_list = git.run_command(["rev-list", "--ancestry-path", hash_range],
                                  split_lines=True)
    if not commit_list:
        logger.info("No commits to convert!")
        return 0

    # Reverse the commit list so we can convert them in chronological order.
    commit_list = list(reversed(commit_list))
    logger.info("Looking at %d commits to convert..." % len(commit_list))
    logger.debug(commit_list)

    # Get some basic P4 workspace info.
    p4_info = p4.run_command(["info"], only1="stat")
    p4_username = p4_info["userName"]
    p4_client = p4_info["clientName"]
    logger.debug("Got P4 user: %s, client: %s" % (p4_username, p4_client))

    # Build the list of files already open in P4. We don't want cross-pollution
    # between changelists.
    files_in_p4_pending_cls = set()
    if not args.ignore_opened:
        logger.debug("Looking for files opened in P4...")
        opened_in_p4 = p4.run_command(["opened"])
        opened_in_p4_entries = p4.get_all_code_entries("stat", opened_in_p4, raise_not_found=False)
        if opened_in_p4_entries:
            where_files = p4.run_command(
                ["where"] + [e["depotFile"] for e in opened_in_p4_entries])
            for where in p4.get_all_code_entries("stat", where_files):
                files_in_p4_pending_cls.add(where["path"])
                logger.debug("  ...opened: %s" % where["path"])

    # Build the list of pending changelists, so we can reuse some of them when
    # we need to "refresh" a changelist with an updated commit.
    p4_reusable_cls = {}
    p4_pending_cls = p4.run_command(["changes", "-l", "-s", "pending", "-c", p4_client])
    for pending_cl in p4.get_all_code_entries("stat", p4_pending_cls, raise_not_found=False):
        p4_reusable_cls[pending_cl["desc"].strip()] = pending_cl["change"]

    # Get the git repo root, to build full file paths.
    git_root = git.run_command(["rev-parse", "--show-toplevel"]).strip().replace("/", os.sep)
    logger.debug("Git repository root: %s" % git_root)

    # Start going through the list!
    ret_status = 0
    last_processed_commit_idx = -1
    rename_detect_arg = '-M'
    if args.rename_threshold:
        rename_detect_arg += str(args.rename_threshold)
    for commit_idx, commit in enumerate(commit_list):
        # Get the commit message.
        commit_msg = git.run_command(["log", "--format=%B", "-n", "1", commit])

        # Get the full paths of files in this commit, along with their status
        # (modified, added, removed).
        commit_diff_files = git.run_command(
            ["diff-tree", "--no-commit-id", "--name-status", "-r", rename_detect_arg, commit],
            split_lines=True)
        git_file_list = []
        git_file_renamed_from_list = []
        for f in commit_diff_files:
            relpaths = tuple(relpath.replace('/', os.sep) for relpath in f.split('\t')[1:])
            if f[0] != "R":
                git_file_renamed_from_list.append(None)
                git_file_list.append(os.path.join(git_root, relpaths[0]))
            else:
                git_file_renamed_from_list.append(os.path.join(git_root, relpaths[0]))
                git_file_list.append(os.path.join(git_root, relpaths[1]))
        git_file_statuses = list([f[0] for f in commit_diff_files])

        git_all_files = git_file_list + list(filter(lambda f: f is not None, git_file_renamed_from_list))

        logger.info("------------------------------------------")
        logger.info("[%s] %s" % (commit, commit_msg.splitlines()[0]))
        logger.debug("  (%s files)" % len(git_file_list))

        # Find a pending changelist with the same message.
        check_file_list = True
        p4_reusable_cl = p4_reusable_cls.get(commit_msg.strip())
        if p4_reusable_cl:
            # We have one! Let's see if it's empty, or if it has the same files
            # as the commit we're converting.
            logger.debug("Found a possible CL to reuse: %s" % p4_reusable_cl)
            p4_opened = p4.run_command(["opened", "-c", p4_reusable_cl])
            opened_files = [e["depotFile"] for e in p4.get_all_code_entries("stat", p4_opened, raise_not_found=False)]
            if opened_files:
                # Get the list of files opened in this changelist.
                where_files = p4.run_command(["where"] + opened_files)
                p4_opened_files = set()
                for p4_f in p4.get_all_code_entries("stat", where_files):
                    p4_opened_files.add(p4_f["path"])

                # Check this list against the commit's file list.
                if p4_opened_files.difference(git_all_files):
                    logger.error(
                        "Found changelist %s with same description as commit %s but different files." %
                        (p4_reusable_cl, commit))
                    logger.info("Commit files: \n%s" % "\n".join(git_all_files))
                    logger.info("Changelist: \n%s" % "\n".join(p4_opened_files))
                    ret_status = 1
                    break

                logger.debug("  ...changelist with the same file list, ok to reuse")
                check_file_list = False
            else:
                logger.debug("  ...empty changelist, ok to reuse")

        # Check if any files are already open in a pending changelist in p4.
        if check_file_list:
            files_already_in_cl = set()
            for f in git_all_files:
                if f in files_in_p4_pending_cls:
                    files_already_in_cl.add(f)
                # Also add these files to our list of files open in p4 since
                # we will open them for edit/add/delete soon.
                files_in_p4_pending_cls.add(f)

            if files_already_in_cl:
                logger.warning(
                    "Commit %s contains the following files, which are already in a pending changelist:" % commit)
                for f in files_already_in_cl:
                    logger.warning(" - %s" % f)
                logger.warning("Ending conversion.")
                ret_status = 1
                break

        # Figure out what files need to be open for edit/add/delete.
        p4_to_add = set()
        p4_to_edit = set()
        p4_to_rename = set()
        p4_to_delete = set()
        for f, old_f, stat in zip(git_file_list, git_file_renamed_from_list, git_file_statuses):
            if stat == 'A':
                p4_to_add.add(f)
            elif stat == 'M':
                p4_to_edit.add(f)
            elif stat[0] == 'R':
                p4_to_rename.add((old_f, f))
            elif stat == 'D':
                p4_to_delete.add(f)
            else:
                logger.error("Unsupported status '%s' for file: %s" % (stat, f))
                ret_status = 1
                break
        if ret_status > 0:
            logger.error("Ending conversion.")
            break

        if p4_to_rename:
            old_local_to_depot_paths = []
            fstat_old_paths = p4.run_command(["fstat"] + [ren[0] for ren in p4_to_rename])
            for entry in p4.get_all_code_entries('stat', fstat_old_paths):
                old_local_to_depot_paths.append(entry['depotFile'])

        if not args.dry_run:
            # Move to the current commit.
            git.run_command(["checkout", commit])

            # Generate a new changelist, or re-use one that has the same description.
            if p4_reusable_cl:
                new_cl_id = p4_reusable_cl
            else:
                changelist_desc = {
                    "Change": "new",
                    "Client": p4_client,
                    "User": p4_username,
                    "Description": commit_msg}

                logger.debug("New changelist:\n%s" % changelist_desc)

                p4_created = p4.run_command(["change", "-i"], stdin=changelist_desc, only1="info")
                new_cl_id = p4.get_created_changelist_id(p4_created)

            # Open/add/delete files in p4.
            for paths, cmd in [
                    (p4_to_add, "add"),
                    (p4_to_edit, "edit"),
                    (p4_to_delete, "delete")]:
                if paths:
                    p4_open_cmdline = [cmd] + list(paths)
                    p4.run_command(p4_open_cmdline)
            # Do moves differently: we need to do them one by one since we need to first make
            # sure the old file is open for edit, and then do the move. Since all of this
            # already happened locally, we need to pass -k to everything.
            for i, (old_path, path) in enumerate(iter(p4_to_rename)):
                old_depot_path = old_local_to_depot_paths[i]
                p4.run_command(["edit", "-k", old_depot_path])
                p4.run_command(["move", "-k", old_path, path])

            # Move all these files into the appropriate changelist.
            p4_reopen_cmdline = ["reopen", "-c", new_cl_id] + git_all_files
            p4.run_command(p4_reopen_cmdline)

            # Shelve this changelist. This is because we may modify the same files
            # when we go back to the original commit.
            if not args.no_shelve:
                p4.run_command(["shelve", "-r", "-c", new_cl_id])

                # Revert the changelist (leaving only the shelf), but pass the -k argument
                # so that the local files keep their changes.
                if not args.no_revert:
                    p4.run_command(["revert", "-k", "-c", new_cl_id, "//..."])
        else:
            logger.info("Would checkout commit %s in git" % commit)
            logger.info("Would create or re-use P4 changelist")
            if p4_to_add:
                logger.info("Would open %d files for add:" % len(p4_to_add))
                for f in p4_to_add:
                    logger.info(" - %s" % f)
            if p4_to_edit:
                logger.info("Would open %d files for edit:" % len(p4_to_edit))
                for f in p4_to_edit:
                    logger.info(" - %s" % f)
            if p4_to_rename:
                logger.info("Would open %d files for move:" % len(p4_to_rename))
                for old_f, f in p4_to_rename:
                    logger.info(" - %s -> %s" % (old_f, f))
            if p4_to_delete:
                logger.info("Would open %d files for delete:" % len(p4_to_delete))
                for f in p4_to_delete:
                    logger.info(" - %s" % f)

        last_processed_commit_idx = commit_idx
        logger.info("")

    # Finished converting commits! If we are auto-managing the `p4` branch,
    # move it to the last converted commmit. If not, print a message for the
    # user to do it themselves.
    logger.info("Converted commits: %d" % (last_processed_commit_idx + 1))
    new_p4_head = commit_list[last_processed_commit_idx]
    if not args.no_p4_branch:
        if not args.dry_run:
            logger.info("Resetting P4 branch to %s.", new_p4_head)
            git.run_command(['checkout', 'p4'])
            git.run_command(['reset', '--hard', new_p4_head])
        else:
            logger.info("Would reset P4 branch to: %s" % new_p4_head)
    else:
        logger.info(
            "You can reset the p4 branch to %s once done "
            "(git checkout p4; git reset --hard %s)" %
            new_p4_head)

    # If we are not told to stay on the last converted commit, return HEAD
    # to what it was before we ran this script.
    return_hash = current_branch or current_head
    if return_hash and not args.stay:
        if not args.dry_run:
            logger.info("Returning HEAD to %s" % return_hash)
            git.run_command(["checkout", return_hash])
        else:
            logger.info("Would return HEAD to %s" % return_hash)
    else:
        logger.warning("Leaving HEAD where it is.")

    # Re-grab the current head, and see if it matches our default branch.
    # If we are auto-managing the `p4` branch and we just finished converting
    # all commits, both the `p4` and default branch are now synced up. Let's
    # switch back to the default branch.
    current_head = git.run_command(["rev-parse", "HEAD"]).strip()
    if not args.no_p4_branch and (current_head == master_hash):
        if not args.dry_run:
            logger.info("Switching back to default branch '%s'." % git_default_branch)
            git.run_command(['checkout', git_default_branch])
        else:
            logger.info("Would switch back to default branch '%s'." % git_default_branch)


if __name__ == '__main__':
    main()

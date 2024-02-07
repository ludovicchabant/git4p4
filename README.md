# git4p4

git4p4 is a command line utility that helps with using Git from inside
a Perforce workspace.

## Motivation

One of Perforce's problems is that it's not practical to work on a series of
commits locally before submitting them to the server. Files being worked on can
be opened for edit in different pending changelists, but it breaks down when the
different changes affect the same files. Shelving changelists only partially
addresses the problem, and it gets quickly inconvenient to use since subsequent
shelves would contain previous changes anyway.

On the other hand, one of Git's strengths is that it can store a series of
changes as local commits, and makes it easy to checkout those changes, amend
them, split them, squash them, and more.

So git4p4 effectively lets you use a local Git repository as a super-charged
Perforce shelf.

## Quickstart

Set things up:

1. Create a Git repository inside your Perforce workspace. 
2. Add all the files to Git as the initial repository commit.
3. Create a `p4` branch at that commit, then switch back to `master` or whatever
   your default branch name is.

Work in Git:

1. Write some code, delete some code, etc.
2. Submit these changes in Git. Do more changes, and commit those too.
3. Eventually you have one or more commits that you are ready to submit via P4.

Convert your Git changes to P4:

This is where git4p4 comes in. By default, running it will:

1. Grab all the commits between `p4` and your current branch head.
2. Try to convert each commit into a P4 changelist.
3. The script stops converting when it detects that a commit contains a file
   that's already in a pending changelist. For instance, commit A contains
   `foo.cpp`, commit B and C contains other files, and commit D also contains
   `foo.cpp`. In this case, git4p4 will convert commits A, B, and C to P4
   changelists, and stop there, since `foo.cpp` is already open for edit in the
   first changelist.
4. Once the script is done, it shelves and reverts all convert P4 changelists,
   moves the `p4` head to the last converted Git commit, and checks out whatever
   commit the Git repository was checked out to before running the script. You
   can pass `-p` (`--p4-work`) to instead stay on the last converted commit,
   with non-reverted pending changelists (_i.e._ staying "in the P4 world").
5. Check out the other git4p4 options!

Continue working by submitting your P4 changelists, doing more work, etc. Rinse
and repeat.

## Gotchas

Watch out for a few things:

* Add a `.gitignore` to your Git repository that matches your `p4ignore`.
* When you sync your workspace in P4, go back to the `p4` branch in the Git
  repository, commit all the new files in Git, and rebase your changes on top of
  that. Generally, watch out about not introducing discrepancies between Git and
  P4.



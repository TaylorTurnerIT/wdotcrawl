import os
import codecs
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
#		pass
#   rm.cleanup()

# Talkative.

class RepoMaintainer:
	def __init__(self, wikidot, path):
		# Settings
		self.wd = wikidot			# Wikidot instance
		self.path = path			# Path to repository
		self.debug = False			# = True to enable more printing
		self.storeRevIds = True		# = True to store .revid with each commit

		# Internal state
		self.wrevs = None			# Compiled wikidot revision list (history)

		self.rev_no	= 0				# Next revision to process
		self.last_names = {}		# Tracks page renames: name atm -> last name in repo
		self.last_parents = {}		# Tracks page parent names: name atm -> last parent in repo


	def _git(self, *args, extra_env=None):
		env = os.environ.copy()
		if extra_env:
			env.update(extra_env)
		result = subprocess.run(['git'] + list(args), cwd=self.path, capture_output=True, text=True, env=env)
		if result.returncode != 0:
			raise Exception('git error: ' + result.stderr)
		return result.stdout

	def _wrevs_path(self):
		return os.path.join(self.path, '.wrevs')

	def _wstate_path(self):
		return os.path.join(self.path, '.wstate')

	#
	# Saves and loads revision list from file
	#
	def saveWRevs(self):
		with open(self._wrevs_path(), 'wb') as fp:
			pickle.dump(self.wrevs, fp)

	def loadWRevs(self):
		with open(self._wrevs_path(), 'rb') as fp:
			self.wrevs = pickle.load(fp)

	#
	# Compiles a combined revision list for a given set of pages, or all pages on the site.
	#  pages: compile history for these pages
	#  depth: download at most this number of revisions.
	#
	# If there exists a cached revision list at the repository destination,
	# it is loaded and no requests are made.
	#
	def buildRevisionList(self, pages=None, depth=10000):
		if os.path.isfile(self._wrevs_path()):
			print("Loading cached revision list...")
			self.loadWRevs()
		else:
			print("Building revision list...")
			if not pages:
				pages = self.wd.list_pages(10000)
			self.wrevs = []
			for page in pages:
				print("Querying page: "+page)
				page_id = self.wd.get_page_id(page)
				print("ID: "+str(page_id))
				revs = self.wd.get_revisions(page_id, depth)
				print("Revisions: "+str(len(revs)))
				for rev in revs:
					self.wrevs.append({
					  'page_id' : page_id,
					  'page_name' : page, # name atm, not at revision time
					  'rev_id' : rev['id'],
					  'date' : rev['date'],
					  'user' : rev['user'],
					  'comment' : rev['comment'],
					})
			self.saveWRevs() # Save a cached copy
			print("")


		print("Total revisions: "+str(len(self.wrevs)))

		print("Sorting revisions...")
		self.wrevs.sort(key=lambda rev: rev['date'])
		print("")

		if self.debug:
			print("Revision list: ")
			for rev in self.wrevs:
				print(str(rev)+"\n")
			print("")


	#
	# Saves and loads operational state from file
	#
	def saveState(self):
		with open(self._wstate_path(), 'wb') as fp:
			pickle.dump(self.rev_no, fp)
			pickle.dump(self.last_names, fp)
			pickle.dump(self.last_parents, fp)

	def loadState(self):
		with open(self._wstate_path(), 'rb') as fp:
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
		self.last_names = {} # Tracks page renames: name atm -> last name in repo
		self.last_parents = {} # Tracks page parent names: name atm -> last parent in repo

		if os.path.isfile(self._wstate_path()):
			print("Continuing from aborted dump state...")
			self.loadState()

		else: # create a new repository (will fail if one exists)
			print("Initializing repository...")
			self._git('init')
			self._git('checkout', '-b', 'main')
			self.rev_no = 0

			if self.storeRevIds:
				# Add revision id file to track per-commit wikidot rev id
				fname = os.path.join(self.path, '.revid')
				codecs.open(fname, "w", "UTF-8").close()
				self._git('add', '.revid')


	#
	# Takes an unprocessed revision from a revision log, fetches its data and commits it.
	# Returns false if no unprocessed revisions remain.
	#
	def commitNext(self):
		if self.rev_no >= len(self.wrevs):
			return False

		rev = self.wrevs[self.rev_no]
		source = self.wd.get_revision_source(rev['rev_id'])
		# Page title and unix_name changes are only available through another request:
		details = self.wd.get_revision_version(rev['rev_id'])

		# Store revision_id for last commit so empty commits (e.g. file uploads) still produce a change.
		revid_fname = os.path.join(self.path, '.revid')
		if self.storeRevIds:
			with codecs.open(revid_fname, "w", "UTF-8") as outp:
				outp.write(rev['rev_id'])

		unixname = rev['page_name']
		rev_unixname = details['unixname'] # may be different in revision than atm

		# Unfortunately, there's no exposed way in Wikidot to see page breadcrumbs at any point in history.
		# The only way to know they were changed is revision comments, though evil people may trick us.
		if rev['comment'].startswith('Parent page set to: "'):
			# This is a parenting revision, remember the new parent
			parent_unixname = rev['comment'][21:-2]
			self.last_parents[unixname] = parent_unixname
		else:
			# Else use last parent_unixname we've recorded
			parent_unixname = self.last_parents[unixname] if unixname in self.last_parents else None
		# There are also problems when parent page gets renamed -- see updateChildren

		# If the page is tracked and its name just changed, tell git
		rename = (unixname in self.last_names) and (self.last_names[unixname] != rev_unixname)
		if rename:
			self.updateChildren(self.last_names[unixname], rev_unixname)
			self._git('mv', str(self.last_names[unixname])+'.txt', str(rev_unixname)+'.txt')

		# Output contents
		fname = os.path.join(self.path, rev_unixname+'.txt')
		with codecs.open(fname, "w", "UTF-8") as outp:
			if details['title']:
				outp.write('title:'+details['title']+'\n')
			if parent_unixname:
				outp.write('parent:'+parent_unixname+'\n')
			outp.write(source)

		# Stage the page file (new or modified) and .revid
		self._git('add', rev_unixname+'.txt')
		if self.storeRevIds:
			self._git('add', '.revid')

		self.last_names[unixname] = rev_unixname

		# Commit
		if rev['comment'] != '':
			commit_msg = rev_unixname + ': ' + rev['comment']
		else:
			commit_msg = rev_unixname

		user = rev['user'] or 'unknown'
		author = '{} <{}@wikidot.invalid>'.format(user, user)

		print("Commiting: "+str(self.rev_no)+'. '+commit_msg)

		# Set both author and committer date via env so history timestamps are accurate
		extra_env = {}
		if rev['date']:
			ts = '@{}'.format(rev['date'])
			extra_env['GIT_AUTHOR_DATE'] = ts
			extra_env['GIT_COMMITTER_DATE'] = ts

		self._git('commit', '--allow-empty', '-m', commit_msg, '--author', author,
		          extra_env=extra_env)

		self.rev_no += 1

		self.saveState() # Update operation state
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
		child_path = os.path.join(self.path, child_unixname+'.txt')
		with codecs.open(child_path, "r", "UTF-8") as f:
			content = f.readlines()
		idx = content.index('parent:'+parent_oldunixname+'\n')
		if idx < 0:
			raise Exception("Cannot update child page "+child_unixname+": "
				+"it is expected to have parent set to "+parent_oldunixname+", but there seems to be no such record in it.")
		content[idx] = 'parent:'+parent_newunixname+'\n'
		with codecs.open(child_path, "w", "UTF-8") as f:
			f.writelines(content)


	#
	# Finalizes the construction process and deletes any temporary files.
	#
	def cleanup(self):
		os.remove(self._wstate_path())
		os.remove(self._wrevs_path())

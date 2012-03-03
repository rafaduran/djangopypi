"""
Management command for adding a package to the repository. Supposed to be the
equivelant of calling easy_install, but the install target is the chishop.
"""

from __future__ import with_statement
from contextlib import contextmanager
import hashlib
import os
import shutil
import tempfile

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import LabelCommand

from optparse import make_option

import pkginfo

from setuptools.package_index import PackageIndex

from djangopypi import conf
from djangopypi.models import Package, Release, Classifier, Distribution

@contextmanager
def tempdir():
    """Simple context that provides a temporary directory that is deleted
    when the context is exited."""
    d = tempfile.mkdtemp(".tmp", "djangopypi.")
    yield d
    shutil.rmtree(d)

class Command(LabelCommand):
    option_list = LabelCommand.option_list + (
            make_option("-o", "--owner", help="add packages as OWNER",
                        metavar="OWNER", default=None),
        )
    help = """Add one or more packages to the repository. Each argument can
be a package name or a URL to an archive or egg. Package names honour
the same rules as easy_install with regard to indicating versions etc.

If a version of the package exists, but is older than what we want to install,
the owner remains the same.

For new packages there needs to be an owner. If the --owner option is present
we use that value. If not, we try to match the maintainer of the package, form
the metadata, with a user in out database, based on the If it's a new package
and the maintainer emailmatches someone in our user list, we use that. If not,
the package can not be
added"""

    def __init__(self, *args, **kwargs):
        self.pypi = PackageIndex()
        LabelCommand.__init__(self, *args, **kwargs)

    def handle_label(self, label, **options):
        with tempdir() as tmp:
            path = self.pypi.download(label, tmp)
            if path:
                self._save_package(path, options["owner"])
            else:
                print "Could not add %s. Not found." % label

    def _save_package(self, path, ownerid):
        meta = self._get_meta(path)

        try:
            # can't use get_or_create as that demands there be an owner
            package = Package.objects.get(name=meta.name)
            isnewpackage = False
        except Package.DoesNotExist:
            package = Package(name=meta.name)
            isnewpackage = True

        release = package.get_release(meta.version)
        if not isnewpackage and release and release.version == meta.version:
            print "%s-%s already added" % (meta.name, meta.version)
            return

        # algorithm as follows: If owner is given, try to grab user with that
        # username from db. If doesn't exist, bail. If no owner set look at
        # mail address from metadata and try to get that user. If it exists
        # use it. If not, bail.
        owner = None

        if ownerid:
            try:
                if "@" in ownerid:
                    owner = User.objects.get(email=ownerid)
                else:
                    owner = User.objects.get(username=ownerid)
            except User.DoesNotExist:
                pass
        else:
            try:
                owner = User.objects.get(email=meta.author_email)
            except User.DoesNotExist:
                pass

        if not owner:
            print "No owner defined. Use --owner to force one"
            return

        # at this point we have metadata and an owner, can safely add it.

        package.owner = owner
        # Some packages don't have proper licence, seems to be a problem
        # with setup.py upload. Use "UNKNOWN"
        package.license = meta.license or "Unknown"
        package.metadata_version = meta.metadata_version
        package.author = meta.author
        package.home_page = meta.home_page
        package.download_url = meta.download_url
        package.summary = meta.summary
        package.description = meta.description
        package.author_email = meta.author_email

        package.save()

        for classifier in meta.classifiers:
            package.classifiers.add(
                    Classifier.objects.get_or_create(name=classifier)[0])
        release = Release()
        release.version = meta.version
        release.package = package
        release.package_info = self._get_pkg_info(meta)

        release.save()

        dis = Distribution()
        dis.release = release

        dis.content.file = open(path, 'rb')
        dis.content.name = settings.DJANGOPYPI_RELEASE_UPLOAD_TO + '/' +\
                path.split('/')[-1]
        # TODO: Very bad hack here, how can I fix it?
        shutil.copy(path, settings.MEDIA_ROOT + '/' + dis.content.name)

        dis.md5_digest = self._get_md5(path)
        dis.filetype = self._get_filetype(path)
        dis.uploader = owner
        dis.comment = ''
        dis.pyversion = meta.requires_python or ''
        dis.signature = ''

        dis.save()
        print "%s-%s added" % (meta.name, meta.version)

    def _get_filetype(self, filename):
        "Returns the package file type, sdist o bdist"
        # TODO: review this, very empiric rules
        if filename.endswith('.zip') or filename.endswith('.tar.gz'):
            return 'sdist'
        elif filename.endswith('.egg'):
            return 'bdist'
        else:
            return 'sdist'

    def _get_md5(self, filename):
        "Returns md5 sum for a given file"
        md5 = hashlib.md5()
        with open(filename, 'rb') as content:
            while(1):
                block = content.read(md5.block_size)
                if not block:
                    break
                md5.update(block)
        return md5.hexdigest()

    def _get_pkg_info(self, meta):
        """
        Transforms metadata from a package to dict usable for MultiValueDict
        instances.
        """
        fields = conf.METADATA_FIELDS[meta.metadata_version]
        metadict = dict([(key, [getattr(meta, key),]) for key in dir(meta)
                if key in fields and not key.startswith('_')])
        return metadict


    def _get_meta(self, path):
        data = pkginfo.get_metadata(path)
        if data:
            return data
        else:
            print "Couldn't get metadata from %s. Not added to chishop" % (
                    os.path.basename(path))
            return None

import os


class PackageNotFoundError(KeyError):
    pass


def get_package_share_directory(package_name):
    # launch_nodes.py imports this but only names it in a comment; nothing is read from the path
    return os.path.join(os.path.dirname(__file__), "share", package_name)

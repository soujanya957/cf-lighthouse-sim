class _Data:
    def __init__(self, data=None):
        self.data = data

    def __repr__(self):
        return f"{type(self).__name__}(data={self.data!r})"


class Bool(_Data):
    pass


class Int32(_Data):
    pass


class Int64(_Data):
    pass


class Float32(_Data):
    pass


class Float64(_Data):
    pass


class String(_Data):
    pass


class Empty:
    pass

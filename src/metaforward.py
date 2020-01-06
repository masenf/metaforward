"""
Forward attribute lookup and method calls
"""
from future.utils import with_metaclass
import six

import collections
from functools import wraps, update_wrapper
import itertools
import inspect
import warnings

import decorator

if six.PY2:
    import funcsigs

    inspect.signature = funcsigs.signature
    inspect.Parameter = funcsigs.Parameter


class NotAMethod(Exception):
    """
    raised when attempting to forward a function that doesn't take `self` as
    its first parameter
    """


def common_subclass(clz, *clzs):
    """
    :param clz: First class (or instance)
    :param clzs: Arbitrary list of classes (or instances)
    :rtype: type
    :return: the most specific common subclass of the given classes
    """
    clz = type(clz) if not isinstance(clz, type) else clz
    clzs = (type(c) if not isinstance(c, type) else c for c in clzs)
    common_bases = inspect.getmro(clz)
    common = common_bases[0]
    for clz in clzs:
        for base in inspect.getmro(clz):
            if base in common_bases:
                common = base
                common_bases = common_bases[common_bases.index(base):]
                break
    return common


def format_function_def(method, parameters):
    """
    :param method: object with a __name__ attribute
    :param parameters: parameter sequence (or comma separated string)
    :return: "name(param1, param2, param3)" as passed to FunctionMaker
    """
    if not isinstance(parameters, six.string_types):
        parameters = ", ".join(parameters)
    return "{}({})".format(method.__name__, parameters)


MethodParametersAndDefaults = collections.namedtuple(
    "MethodParametersAndDefaults", ("parameters", "defaults"),
)


def method_signature_and_defaults(method):
    """
    :param method: a callable method
    :return: tuple of (tuple of parameters, tuple of defaults)
    """
    try:
        method_sig = inspect.signature(method)
    except ValueError:
        # if we can't get the signature for whatever reason, fallback
        return ("self", "*args", "**kwargs"), ()

    if list(method_sig.parameters.keys())[0] != "self":
        # can only forward instance methods
        raise NotAMethod(format_function_def(method, str(method_sig)))

    parameters = []
    defaults = []
    for param in method_sig.parameters.values():
        parameters.append(str(param))
        if param.default is not inspect.Parameter.empty:
            defaults.append(param.default)

    return MethodParametersAndDefaults(tuple(parameters), tuple(defaults))


def method_forwarder(attr, method):
    """
    :param attr: the name of the attribute to forward
    :param method: the method being forwarded (wrapped)
    :return: a callable proxy that will invoke `self._forward` to return a list of
             results when called
    """
    try:
        parameters, defaults = method_signature_and_defaults(method)
    except NotAMethod:
        return
    method_def = format_function_def(method, parameters)
    method_attrs = {
        k: getattr(method, k)
        for k in dir(method)
        if k.startswith("__") and not callable(getattr(method, k))
    }
    return decorator.FunctionMaker.create(
        method_def,
        "return self._forward(attr)({})".format(", ".join(parameters[1:])),
        dict(attr=attr),
        defaults=defaults,
        doc=method_attrs.pop("__doc__", None),
        module=method_attrs.pop("__module__", None),
        **method_attrs
    )


def property_forwarder(attr, value):
    """
    :param attr: the name of the attribute to forward
    :param value: the value being forwarded (used to extract docstring)
    :return: a property which will invoke `self._forward` to return a list of results
             when accessed
    """

    def proxy(self):
        return self._forward(attr)

    return property(proxy, doc=value.__doc__)


def forwarder(attr, value):
    """
    Convenience wrapper returns either a `method_forwarder` or a `property_forwarder`
    depending on whether `value` is callable.
    """
    if callable(value):
        return method_forwarder(attr, value)
    return property_forwarder(attr, value)


class TypedForwarderMeta(type):
    """
    Warning: grey Magic ahead

    TypedForwarderMeta dynamically generates Forwarder subclasses with explicitly
    defined properties and methods introspected from the proxied class.

    This metaclass recognizes the class attribute PROXY_ONTO when defining subclasses
    of Forwarder to dynamically add explicit forwarded properties and methods for a
    specific type of item.

    Forwarding is still dynamic as these classes are never defined in code, however
    introspection tools, IDEs, and REPL will be able to identify known methods and
    attributes.

    Forwarder doesn't perform runtime type checking, so it's still possible to get
    AttributeError in heterogeneous lists.
    """

    # Autogenerated Forwarder subclasses are stored here. Keys are returned
    # by the `_typed_key` staticmethod
    TypedForwarder = {}
    # Forwarder subclasses may specify the proxy type statically at define time
    PROXY_ONTO_TAG = "PROXY_ONTO"
    # Forwarder subclasses may explicitly ignore attributes on proxied types
    IGNORED_ATTRIBUTES_TAG = "IGNORED_ATTRIBUTES"

    @classmethod
    def ignored_attributes_from_bases(mcs, bases, dct=None):
        """
        Collect all explicitly IGNORED_ATTRIBUTES on each class in the inheritence chain

        :param bases: sequence of types
        :param dct: optional dict of attributes for a class under construction
        :return: set of attributes which should not be proxied
        """
        ignored_attributes = set()
        if dct is not None:
            ignored_attributes.update(dct.get(mcs.IGNORED_ATTRIBUTES_TAG, tuple()))
        for base in bases:
            ignored_attributes.update(getattr(base, mcs.IGNORED_ATTRIBUTES_TAG, tuple()))
        return ignored_attributes

    def ignored_attributes_from_cls(cls):
        """
        Helper for ignored_attributes_from_bases that is used when dynamically
        generating a class rather than intercepting the creation of a new class

        :return: set of attributes which should not be proxied
        """
        return cls.ignored_attributes_from_bases(inspect.getmro(cls))

    @classmethod
    def shadowed_attributes_from_bases(mcs, bases, dct=None):
        """
        Collect attributes from a base class and the dct of a class under construction.

        :param bases: sequence of types
        :param dct: optional dict of attributes for a class under construction
        :return: set of attributes which should not be proxied
        """
        shadowed_attributes = set()
        if dct is not None:
            shadowed_attributes.update(dct.keys())
        for base in bases:
            shadowed_attributes.update(dir(base))
        return shadowed_attributes

    def shadowed_attributes_from_cls(cls):
        """
        Helper for ignored_attributes_from_bases that is used when dynamically
        generating a class rather than intercepting the creation of a new class

        :return: set of attributes which should not be proxied
        """
        return cls.shadowed_attributes_from_bases(inspect.getmro(cls))

    @staticmethod
    def _typed_key(forwarder_cls, proxy_onto_type):
        return "{}__{}".format(forwarder_cls.__name__, proxy_onto_type.__name__)

    @staticmethod
    def _orig_base(name, bases):
        """
        :param name: The name of the class being created
        :param bases: Sequence of base classes for the class being created
        :return: The most recent Forwarder base class
        :raises: TypeError if none of the bases are subclass of Forwarder
        """
        if name in ("Forwarder", "ForwarderList"):
            return bases[0]
        try:
            # determine the most recent subclass of Forwarder
            return next(b for b in bases if issubclass(b, Forwarder))
        except StopIteration:
            raise TypeError(
                "Metaclass Error: {!r} does not inherit from {!r}. Actual bases: {!r}".format(
                    name, Forwarder, bases,
                ),
            )

    @staticmethod
    def _common_type_from_sequence(seq):
        """
        :param seq: a sequence of objects
        :return: The most-specific common subclass of all items in seq
        """
        try:
            first_item = seq[0]
            return common_subclass(first_item, *seq[1:])
        except IndexError:
            warnings.warn("Cannot determine proxy_onto type from an empty sequence")
            return object

    @staticmethod
    def _forward_proxy_for(proxy_onto_type):
        """
        Create a list of forwarder methods for forwarding attribute and method access
        from a Forwarder to a sequence of `proxy_onto_type` objects.

        Wrappers for dunder methods are not generated in an attempt to preserve sanity.

        :param proxy_onto_type: The object type to proxy attribute and method access for
        :return: dict of {attribute: proxied_method_or_property}
        """
        orig_attributes = {
            attr: getattr(proxy_onto_type, attr)
            for attr in dir(proxy_onto_type)
            if not attr.startswith("__")
        }
        # wrap normal attributes and methods
        wrapped = {
            attr: forwarder(attr, value) for attr, value in orig_attributes.items()
        }
        # wrap attrs attributes
        wrapped.update(
            {
                attr.name: forwarder(attr.name, None)
                for attr in getattr(proxy_onto_type, "__attrs_attrs__", tuple())
            },
        )
        return {k: v for k, v in wrapped.items() if v is not None}

    @staticmethod
    def _generate_warn_getattr(real_getattr, proxy_onto_type):
        """
        :param real_getattr: Reference to the parent class __getattr__ method
        :param proxy_onto_type: The object type to proxy attribute and method access for
        :return: __getattr__ method that raises a warning when the attribute is not
                explicitly defined (but still forwards lookup anyway)
        """

        def __getattr__(self, attr):
            result = real_getattr(self, attr)
            warnings.warn(
                "{!r} is not a forwarded attribute of {!r} ({!r})".format(
                    attr, proxy_onto_type, self,
                ),
            )
            return result

        return __getattr__

    @classmethod
    def _generate_subclass_attributes(
        mcs,
        forwarder_cls,
        proxy_onto_type,
        shadowed_attributes=None,
        ignored_attributes=None,
    ):
        """
        :param forwarder_cls: Base class for the new Forwarder subclass
        :param proxy_onto_type: The object type to proxy attribute and method access for
        :param ignored_attributes: Optional set of attributes to not forward. If not
                specified will default to all defined and explicitly IGNORED attributes
                of forwarder_cls and parent classes
        :return: dict of attributes for a new Forwarder class
        """
        if ignored_attributes is None:
            ignored_attributes = mcs.ignored_attributes_from_cls(forwarder_cls)
        if shadowed_attributes is None:
            shadowed_attributes = mcs.shadowed_attributes_from_cls(forwarder_cls)
        proxies = mcs._forward_proxy_for(proxy_onto_type)
        new_attributes = {
            a: v
            for a, v in proxies.items()
            if a not in ignored_attributes.union(shadowed_attributes)
        }
        # if the Forwarder shadows attributes from the target object, those can be
        # called with a trailing underscore
        new_attributes.update(
            {a + "_": v for a, v in proxies.items() if a in shadowed_attributes},
        )
        new_attributes[mcs.PROXY_ONTO_TAG] = proxy_onto_type
        new_attributes["__getattr__"] = mcs._generate_warn_getattr(
            forwarder_cls.__getattr__, proxy_onto_type,
        )
        return new_attributes

    @classmethod
    def _generate_typed_forwarder(cls, forwarder_cls, proxy_onto_type):
        """
        Dynamically generate a new TypedForwarderForX subclass where X is proxy_onto_type

        :param forwarder_cls: Base class for the new Forwarder subclass
        :param proxy_onto_type: The object type to proxy attribute and method access for
        :return: Forwarder subclass specialized for proxy_onto_type access
        """
        if proxy_onto_type is object:
            # no type specialization for object
            return forwarder_cls
        name = "Typed{}For{}".format(forwarder_cls.__name__, proxy_onto_type.__name__)
        return type(
            name,
            (forwarder_cls,),
            cls._generate_subclass_attributes(forwarder_cls, proxy_onto_type),
        )

    @classmethod
    def _typecheck_proxy_onto(mcs, forwarder_cls, proxy_onto_type):
        """
        :param forwarder_cls: Base class for the new Forwarder subclass
        :param proxy_onto_type: The object type to proxy attribute and method access for
        :return: None
        :raises: TypeError if proxy_onto_type is not a type or not subclass of the
                 forwarder_cls' PROXY_ONTO type
        """
        if not isinstance(proxy_onto_type, type):
            raise TypeError(
                "proxy_onto must be a type, not {!r}".format(type(proxy_onto_type)),
            )
        cls_proxy_onto = getattr(forwarder_cls, mcs.PROXY_ONTO_TAG, object)
        if not issubclass(proxy_onto_type, cls_proxy_onto):
            raise TypeError(
                "Metaclass Error: {!r} cannot proxy_onto {!r} "
                "because {!r} is not a subclass of {!r}".format(
                    forwarder_cls, proxy_onto_type, proxy_onto_type, cls_proxy_onto,
                ),
            )

    def __new__(mcs, name, bases, dct):
        """
        Called when creating subclasses of Forwarder.

        Handles the class attribute PROXY_ONTO, which is used in exactly the same way as
        `proxy_onto`, to create statically defined subclasses of Forwarder to proxy
        attribute and method access onto a specific type.

        If a subclass doesn't specify PROXY_ONTO, then this method is essentially a no-op.

        :param name: The name of the class being created
        :param bases: The parents of the class being created
        :param dct: The attributes of the class being created
        """
        proxy_onto = dct.get(mcs.PROXY_ONTO_TAG, None)
        if proxy_onto:
            orig_base = mcs._orig_base(name, bases)
            mcs._typecheck_proxy_onto(orig_base, proxy_onto)
            dct.update(
                mcs._generate_subclass_attributes(
                    forwarder_cls=orig_base,
                    proxy_onto_type=proxy_onto,
                    shadowed_attributes=mcs.shadowed_attributes_from_bases(bases, dct),
                    ignored_attributes=mcs.ignored_attributes_from_bases(bases, dct),
                ),
            )
        return super(TypedForwarderMeta, mcs).__new__(mcs, name, bases, dct)


class Forwarder(with_metaclass(TypedForwarderMeta)):
    """
    Forwarder is a base class which forwards attribute and method access onto a target
    object passed to the initializer.

    Set PROXY_ONTO class attribute as the class of the target object to dynamically
    generate wrapper properties and methods which match the signature and docstring
    of the target class.
    """

    def __init__(self, target):
        """
        :param target: the item to forward attribute and method lookup onto
        """
        self._forward_target = target

    def _forward(self, attr):
        """
        Forward attribute lookup for `attr` onto each element of the list.

        :param attr: name of the attribute to forward
        :return: the attribute from the underlying _forward_target
        """
        return getattr(self._forward_target, attr)

    def __getattr__(self, attr):
        return self._forward(attr)

    def __call__(self, *args, **kwargs):
        """
        Forward callable onto each item of the list
        """
        return self._forward("__call__")(*args, **kwargs)

    def __enter__(self):
        """
        Forward contextmanager protocol onto each item of the list.

        This has the same caveats as the deprecated `contextlib.nested` implementation:
        https://docs.python.org/2/library/contextlib.html#contextlib.nested

        :param self:
        :return:
        """
        return self._forward("__enter__")()

    def __exit__(self, etype, evalue, traceback):
        suppress_exception = False
        for ex in self._forward_attribute("__exit__"):
            if ex(etype, evalue, traceback):
                suppress_exception = True
        return suppress_exception


class TypedForwarderListMeta(TypedForwarderMeta):
    """
    This metaclass adds a keyword argument `proxy_onto` to the ForwarderList
    constructor/initializer which will create a dynamic `TypedForwarderListForX` where
    'X' is the type of items in the list.
    """

    # ForwarderList subclasses may specify True to automatically register themselves as
    # the handler for their type in all parent ForwarderList classes
    DEFAULT_PROXY_TAG = "DEFAULT_PROXY"

    def __call__(cls, iterable, *args, **kwargs):
        """
        Called when creating new instances of ForwarderList classes and subclasses.

        This method intercepts the `proxy_onto` parameter to `__init__` and
        dynamically changes the class being instantiated to a type-specific Forwarder
        that has the same attributes and methods of `proxy_onto` type.

        :param iterable: Iterable passed to Forwarder initializer. If proxy_onto is
                         True, this iterable will be consumed to determine the common
                         type of all items yielded by it, and a tuple will be passed to
                         Forwarder initializer instead.
        :param proxy_onto: The common type of all items in the Forwarder. If True,
                           attempt to automatically detect the common type of all items
                           in iterable.
        :return: Initialized instance of Forwarder (or subclass)
        """
        forwarder_cls = cls
        proxy_onto = kwargs.get("proxy_onto", None)
        if proxy_onto is True:
            # Collapse the iterable to get the common type of the sequence
            if not isinstance(iterable, collections.Sequence):
                # if we have an iterator, make sure to save the values to later
                # instantiate the list!
                iterable = tuple(iterable)
            proxy_onto = cls._common_type_from_sequence(iterable)
        if proxy_onto:
            cls._typecheck_proxy_onto(forwarder_cls, proxy_onto)
            forwarder_cls = cls.TypedForwarder.setdefault(
                cls._typed_key(forwarder_cls, proxy_onto),
                cls._generate_typed_forwarder(
                    forwarder_cls=forwarder_cls, proxy_onto_type=proxy_onto,
                ),
            )
        return super(TypedForwarderListMeta, forwarder_cls).__call__(
            iterable, *args, **kwargs
        )

    def __new__(mcs, name, bases, dct):
        """
        Called when creating subclasses of ForwarderList.

        Handles the class attribute DEFAULT_PROXY, which registers this subclass as
        the default handler for it's proxied type when creating a new ForwarderList
        containing that subtype
        """
        proxy_onto = dct.get(mcs.PROXY_ONTO_TAG, None)
        # pop DEFAULT_PROXY so that subclasses don't inherit it
        default_proxy = dct.pop(mcs.DEFAULT_PROXY_TAG, False)
        new_class = super(TypedForwarderListMeta, mcs).__new__(mcs, name, bases, dct)
        if proxy_onto and default_proxy:
            for cls in bases + (new_class,):
                if isinstance(cls, mcs):
                    mcs.TypedForwarder[mcs._typed_key(cls, proxy_onto)] = new_class
        return new_class


class ForwarderList(with_metaclass(TypedForwarderListMeta, list, Forwarder)):
    """
    Forward arbitrary attribute access on the list to each item of the list
    and return a ForwarderList containing the results.
    """

    def __init__(self, iterable, proxy_onto=None):
        """
        :param iterable: The iterable to seed the IterList with
        :param proxy_onto: The class of objects in the list -- This is interpreted by
               the TypedForwarderMeta class
        """
        super(ForwarderList, self).__init__(iterable)
        self.proxy_onto = (
            proxy_onto
            if proxy_onto
            else getattr(self, TypedForwarderMeta.PROXY_ONTO_TAG, None)
        )

    def _forward_attribute(self, attr):
        return [getattr(x, attr) for x in self]

    def _forward_method(self, methods):
        def wrapper(*args, **kwargs):
            return ForwarderList(
                [m(*args, **kwargs) for m in methods], proxy_onto=bool(self.proxy_onto),
            )

        if methods:
            wrapper = update_wrapper(wrapper, methods[0])

        return wrapper

    def _forward(self, attr):
        """
        Forward attribute lookup for `attr` onto each element of the list.

        :param attr: name of the attribute to forward
        :return: ForwarderList wrapping the result for non-callable attributes or
                 Arbitrary callable returning a ForwarderList wrapping the result of
                 calling the underlying method
        """
        results = self._forward_attribute(attr)
        if results and all([callable(r) for r in results]):
            return self._forward_method(results)
        if results:
            # Normal case, return a ForwarderList with the results
            return ForwarderList(results, proxy_onto=bool(self.proxy_onto))
        # Empty list
        return results

    def _scatter_method(self, methods):
        """
        Scatter iterables across forwarded method call

        :param methods: sequence of bound methods
        :return:
        """

        def iterable_arg(a):
            if isinstance(a, six.string_types):
                return itertools.cycle((a,))
            try:
                return itertools.cycle(a)
            except TypeError:
                return itertools.cycle((a,))

        def iterable_kwarg(k, v):
            if k.endswith("__"):
                # double underscore isn't escaped
                return k[:-1], iterable_arg(v)
            if k.endswith("_"):
                return k[:-1], itertools.cycle((v,))
            return k, iterable_arg(v)

        if not all([callable(m) for m in methods]):
            raise RuntimeError(
                "Cannot scatter onto non-callable attribute {!r}".format(
                    [m for m in methods if not callable(m)],
                ),
            )

        def wrapper(*args, **kwargs):
            argset = [iterable_arg(a) for a in args]
            arggen = (tuple(next(ai) for ai in argset) for _ in methods)
            kwargset = dict(iterable_kwarg(k, v) for k, v in kwargs.items())
            kwarggen = ({k: next(v) for k, v in kwargset.items()} for _ in methods)
            return ForwarderList(
                [
                    m(*iterargs, **iterkwargs)
                    for m, iterargs, iterkwargs in zip(methods, arggen, kwarggen)
                ],
                proxy_onto=bool(self.proxy_onto),
            )

        if methods:
            wrapper = update_wrapper(wrapper, methods[0])
        return wrapper

    @property
    def scatter(self):
        """
        Get a copy of the current ForwarderList that forwards iterable arguments to
        method calls across the elements of the list.
        """
        scatter_forwarder = type(self)(self)
        scatter_forwarder._forward_method = scatter_forwarder._scatter_method
        return scatter_forwarder

    def __getitem__(self, item):
        """
        Override __getitem__ to return a base ForwarderList for the slice.

        This is a way to convert a ReducingForwarderList into a regular ForwarderList.

        Any default ForwarderList subclass however will be used instead of the
        base ForwarderList class if proxy_onto is specified
        """
        selection = super(ForwarderList, self).__getitem__(item)
        if isinstance(item, slice):
            return ForwarderList(selection, proxy_onto=bool(self.proxy_onto))
        return selection


class ReducingForwarderList(ForwarderList):
    """
    A ForwarderList that returns a bare item rather than a ForwarderList if the length
    of the resulting list is 1
    """

    def _forward(self, attr):
        return self._reduce(super(ReducingForwarderList, self)._forward(attr))

    @staticmethod
    def _reduce(sequence):
        """
        If the sequence has 1 item, return sequence[0]
        """
        if callable(sequence) and not isinstance(sequence, ForwarderList):

            @wraps(sequence)
            def wrapper(*args, **kwargs):
                return ForwarderList._reduce(sequence(*args, **kwargs))

            return wrapper

        try:
            # sequence may be an iterlist.IterList and we wouldn't
            # want to resolve the whole thing
            sequence[1]
            return sequence
        except IndexError:
            return sequence[0]
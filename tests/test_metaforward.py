# XXX: python 2 / 3 compatibility
from future.utils import with_metaclass

import random

import attr
import pytest

from metaforward import ForwarderList, TypedForwarderListMeta


class CalledIgnoredAttribute(Exception):
    pass


@attr.s
class Item(object):
    """
    Item will be in the ForwarderList
    """
    class_attribute = "class_attribute"

    nesting_level = attr.ib(default=0)
    identifier = attr.ib(factory=random.random)

    def __attrs_post_init__(self):
        # TypedForwarderList cannot introspect dynamically set instance attributes
        self.dynamic_attribute = "dynamic_attribute"
        self.dynamic_token = "Token{}".format(random.random())

    @property
    def instance_property(self):
        return "instance_property", self.nesting_level

    @property
    def token(self):
        return self.dynamic_token

    @classmethod
    def class_method(cls, arg):
        return arg

    def method(self, *args, **kwargs):
        return args, kwargs

    def recursive(self, bump=1):
        return Item(nesting_level=self.nesting_level + bump, identifier=self.identifier)

    def _forward(self):
        raise CalledIgnoredAttribute("_forward of Item should never be called by the forwarder")

    def _reduce(self):
        raise CalledIgnoredAttribute("_reduce of Item should never be called by the forwarder")


class CallableItem(Item):
    def __call__(self, arg):
        return arg


class ContextItem(Item):
    def __enter__(self):
        return self.identifier

    def __exit__(self, etype, evalue, traceback):
        if evalue and evalue.args and evalue.args[0] == self.identifier:
            # suppress the exception if it matches my identifier
            return True
        return


class SubItem(Item):
    @property
    def sub_property(self):
        return self.token


class SubItem2(Item):
    @property
    def sub_property(self):
        return self.token


class NotAnItem(object):
    class_attribute = "NotAnItem class_attribute"


class StaticItemForwarderList(ForwarderList):
    PROXY_ONTO = Item


class SubclassItemForwarderList(StaticItemForwarderList):
    """
    This class exists to ensure that the MRO base chain isn't mucked with
    for subclasses of dynamic PROXY_ONTO TypedForwarderForX classes.

    Classes that are not defined with their own PROXY_ONTO should be left alone
    """


class IgnoreTokenItemForwarderList(ForwarderList):
    PROXY_ONTO = Item
    IGNORED_ATTRIBUTES = ("token", )

    @property
    def dynamic_token(self):
        return "Shadowing a dynamic attribute in the target class"


@pytest.fixture(params=[None, Item, True],
                ids=["untyped", "typed", "typed_auto"],
                scope="class")
def forwarderlist_of_item(request):
    kwargs = {}
    if request.param is not None:
        kwargs["proxy_onto"] = request.param
    return ForwarderList((Item() for i in range(100)), **kwargs)


class TestForwarderList(object):
    @staticmethod
    def assert_forwarded_attribute(forwarder_list, attribute, exp_value, exp_list_type, dynamic=False):
        if type(forwarder_list) is ForwarderList or dynamic:
            assert not hasattr(type(forwarder_list), attribute)
        else:
            assert hasattr(type(forwarder_list), attribute)
        forwarded = getattr(forwarder_list, attribute)
        if type(forwarder_list) is ForwarderList:
            assert type(forwarded) is ForwarderList
        else:
            assert type(forwarded).__name__ == exp_list_type
        for value in forwarded:
            assert value == exp_value

    @staticmethod
    def assert_forwarded_callable(forwarder_list, attribute, exp_value, exp_list_type, *args, **kwargs):
        if type(forwarder_list) is ForwarderList:
            assert not hasattr(type(forwarder_list), attribute)
        else:
            assert hasattr(type(forwarder_list), attribute)
        forwarded = getattr(forwarder_list, attribute)
        assert callable(forwarded)
        results = forwarded(*args, **kwargs)
        if type(forwarder_list) is ForwarderList:
            assert type(results) is ForwarderList
        else:
            assert type(results).__name__ == exp_list_type
        for value in results:
            assert value == exp_value

    def test_forward_class_attribute(self, forwarderlist_of_item):
        self.assert_forwarded_attribute(forwarderlist_of_item, "class_attribute", Item.class_attribute, "TypedForwarderListForstr")

    def test_forward_attrs_instance_attribute(self, forwarderlist_of_item):
        self.assert_forwarded_attribute(forwarderlist_of_item, "nesting_level", exp_value=0, exp_list_type="TypedForwarderListForint")

    def test_forward_instance_property(self, forwarderlist_of_item):
        self.assert_forwarded_attribute(forwarderlist_of_item, "instance_property", exp_value=("instance_property", 0), exp_list_type="TypedForwarderListFortuple")

    def test_forward_dynamic_instance_attribute(self, forwarderlist_of_item):
        self.assert_forwarded_attribute(forwarderlist_of_item, "dynamic_attribute", exp_value="dynamic_attribute", exp_list_type="TypedForwarderListForstr", dynamic=True)

    @pytest.mark.skip("class method forwarding is not yet implemented")
    def test_forward_class_method(self, forwarderlist_of_item):
        exp_value = range(10)
        self.assert_forwarded_callable(forwarderlist_of_item, "class_method", exp_value, "TypedForwarderListForrange", exp_value)

    def test_forward_instance_method(self, forwarderlist_of_item):
        exp_value = ((ForwarderList, ), {"rval": random.random()})
        self.assert_forwarded_callable(forwarderlist_of_item, "method", exp_value, "TypedForwarderListFortuple", *exp_value[0], **exp_value[1])

    def test_forward_ignored_attributes(self, forwarderlist_of_item):
        with pytest.raises(CalledIgnoredAttribute):
            forwarderlist_of_item._forward("_forward")()
        if type(forwarderlist_of_item) != ForwarderList:
            # shadow forwarding doesn't work on untyped ForwarderList
            with pytest.raises(CalledIgnoredAttribute):
                forwarderlist_of_item._forward_()
        with pytest.raises(TypeError):
            forwarderlist_of_item._forward()

    def test_forward_recursive(self, forwarderlist_of_item):
        n_items = len(forwarderlist_of_item)
        results = forwarderlist_of_item
        for it in range(100):
            assert len(results) == n_items
            assert all(nl == it for nl in results.nesting_level)
            results = results.recursive()
            if type(forwarderlist_of_item) is ForwarderList:
                assert type(results) is ForwarderList
            else:
                assert type(results).__name__ == "TypedForwarderListForItem"

    def test_common_type(self):
        forwarder = ForwarderList((Item(), SubItem(), SubItem2()))
        assert type(forwarder).__name__ == "ForwarderList"
        typed_forwarder = ForwarderList((Item(), SubItem(), SubItem2()), proxy_onto=True)
        assert type(typed_forwarder).__name__ == "TypedForwarderListForItem"
        assert not hasattr(type(typed_forwarder), "sub_property")
        with pytest.raises(AttributeError):
            typed_forwarder.sub_property

        subtyped_forwarder = ForwarderList((SubItem(), SubItem2()), proxy_onto=True)
        assert type(subtyped_forwarder).__name__ == "TypedForwarderListForItem"
        assert not hasattr(type(subtyped_forwarder), "sub_property")
        for sp, tok in zip(subtyped_forwarder.sub_property, subtyped_forwarder.token):
            assert sp == tok

        subtyped_forwarder1 = ForwarderList((SubItem(), SubItem()), proxy_onto=True)
        assert type(subtyped_forwarder1).__name__ == "TypedForwarderListForSubItem"
        assert hasattr(type(subtyped_forwarder1), "sub_property")
        for sp, tok in zip(subtyped_forwarder1.sub_property, subtyped_forwarder1.token):
            assert sp == tok

        subtyped_forwarder2 = ForwarderList((SubItem2(), SubItem2()), proxy_onto=True)
        assert type(subtyped_forwarder2).__name__ == "TypedForwarderListForSubItem2"
        assert hasattr(type(subtyped_forwarder2), "sub_property")
        for sp, tok in zip(subtyped_forwarder2.sub_property, subtyped_forwarder2.token):
            assert sp == tok

        typed_forwarder.append(NotAnItem())
        gen_forwarder = ForwarderList(typed_forwarder, proxy_onto=True)
        assert type(gen_forwarder).__name__ == "ForwarderList"
        for v in gen_forwarder.class_attribute:
            assert "class_attribute" in v

    def test_callable(self):
        callable_forwarder = ForwarderList((CallableItem() for _ in range(10)))
        results = callable_forwarder(42)
        assert all([r == 42 for r in results])

    def test_context(self):
        context_forwarder = ForwarderList((ContextItem(identifier=ix) for ix in range(10)))
        with context_forwarder as identifiers:
            for i, idf in enumerate(identifiers):
                assert i == idf

    def test_context_raise(self):
        context_forwarder = ForwarderList((ContextItem(identifier=ix) for ix in range(10)))
        with context_forwarder as identifiers:
            raise Exception(identifiers[0])

        with pytest.raises(Exception):
            with context_forwarder as identifiers:
                raise Exception("Uh oh")

    def test_scatter(self):
        callable_forwarder = ForwarderList((CallableItem() for _ in range(10)))
        results = callable_forwarder.scatter((range(10)))
        for i, r in enumerate(results):
            assert i == r

class TestStaticTypedForwarderList(object):
    def test_subclass_mro(self):
        assert StaticItemForwarderList.__mro__ == SubclassItemForwarderList.__mro__[1:]

    def test_static_parent_not_rewritten(self):
        assert StaticItemForwarderList.__mro__[1] is ForwarderList

    def test_proxy_onto(self):
        assert StaticItemForwarderList.PROXY_ONTO == Item
        assert SubclassItemForwarderList.PROXY_ONTO == Item
        assert IgnoreTokenItemForwarderList.PROXY_ONTO == Item

    def test_ignored_attributes(self):
        regular_forwarder = StaticItemForwarderList((Item(), ))
        ignored_forwarder = IgnoreTokenItemForwarderList(regular_forwarder)

        assert hasattr(type(regular_forwarder), "token")
        assert not hasattr(type(ignored_forwarder), "token")

        # even though the attribute is ignored, it can still be forwarded if nothing in the
        assert regular_forwarder.token[0] == ignored_forwarder.token[0]

        # ignored_forwarder specifically shadows dynamic_token
        assert regular_forwarder.dynamic_token[0] != ignored_forwarder.dynamic_token[0]

    def test_proxy_onto_subtype(self):
        regular_forwarder = StaticItemForwarderList((SubItem(),))

        assert not hasattr(type(regular_forwarder), "sub_property")

        sub_forwarder = StaticItemForwarderList(regular_forwarder, proxy_onto=SubItem)

        assert hasattr(type(sub_forwarder), "sub_property")
        assert regular_forwarder.sub_property[0] == sub_forwarder.sub_property[0]

    def test_proxy_onto_non_subtype(self):
        regular_forwarder = StaticItemForwarderList((SubItem(),))

        assert not hasattr(type(regular_forwarder), "sub_property")

        with pytest.raises(TypeError):
            sub_forwarder = StaticItemForwarderList(regular_forwarder, proxy_onto=NotAnItem)


def test_metaclass_inheriting_from_non_forwarderlist():
    class Blah(with_metaclass(TypedForwarderListMeta)):
        pass
    with pytest.raises(TypeError):
        class BlahSub(Blah):
            PROXY_ONTO = Item

def test_subclass_inheriting_with_non_common_proxy_onto():
    with pytest.raises(TypeError):
        class BadSubclass(StaticItemForwarderList):
            PROXY_ONTO = NotAnItem
from app.indexing.chunking import chunk_repository
from app.retrieval.symbols import SymbolTable, find_named_symbols, get_direct_callees, is_distinctive


SHA = "a" * 40

FILES = {
    "src/shop/cart.py": '''from .utils import format_price


class Cart:
    """A shopping cart."""

    def add_item(self, item):
        self.items.append(item)
        self.update_total()

    def update_total(self):
        self.total = compute_total(self.items)

    def show_total(self):
        return format_price(self.total)

    def get(self, key):
        return self.items[key]

    def remove_discount_code(self):
        def inner():
            return self.secret_helper()
        return inner


def compute_total(items):
    return sum(item.price for item in items)


def get(cart_id):
    return Cart()
''',
    "src/shop/utils.py": "def format_price(value):\n    return f'{value:.2f}'\n",
    "src/shop/api.py": "def checkout(cart):\n    return cart\n",
    "src/shop/orders.py": "class Order:\n    def get(self):\n        pass\n",
    # A mixin whose method calls self.checkout(), which only the subclass implements
    "src/shop/mixins.py": '''class CheckoutMixin:
    def finish_order(self):
        return self.place_order()

    def place_order(self):
        ...


class Store(CheckoutMixin):
    def place_order(self):
        return "placed"
''',
}
CHUNKS = chunk_repository(FILES, SHA)
TABLE = SymbolTable.build(CHUNKS, FILES)


def matches(question):
    return [(match.symbol, match.score) for match in find_named_symbols(question, TABLE, FILES)]


def test_distinctive_names():
    assert is_distinctive("add_item") and is_distinctive("HTTPAdapter") and is_distinctive("_basic_auth_str")
    assert not is_distinctive("get") and not is_distinctive("request") and not is_distinctive("__init__")


def test_a_method_named_with_its_class_scores_highest_and_brings_its_helpers():
    found = matches("How does Cart.add_item work?")

    assert found[0] == ("Cart.add_item", 30)
    # add_item calls self.update_total(), so the helper comes along with a lower score
    # (10, and no extra points because "update total" isn't in the question)
    assert ("Cart.update_total", 10) in found


def test_a_distinctive_name_on_its_own_matches():
    assert matches("What does compute_total return?")[0] == ("compute_total", 25)


def test_generic_words_alone_do_not_match_functions():
    # "get" appears in the question but is far too generic to pick Cart.get or get()
    assert matches("How do I get the cart?") == []


def test_package_qualified_names_only_match_top_level_functions():
    # shop.get is the module function, not Cart.get or Order.get.
    # get() calls Cart(), so the class it creates comes along as a helper
    assert matches("What does shop.get return?") == [("get", 30), ("Cart", 10)]


def test_distinctive_class_name_plus_method_words():
    found = dict(matches("How does CheckoutMixin finish an order?"))

    assert found["CheckoutMixin.finish_order"] == 20 + 2  # finish, order


def test_one_word_class_name_plus_method_words_is_not_enough():
    # "Cart" could just be the start of a sentence, so it doesn't pick its methods
    assert matches("Cart remove a discount code?") == []


def test_one_word_classes_need_to_be_qualified():
    # "Order" starts a normal sentence here; a one-word class name isn't enough on its own
    assert matches("Order matters when adding items") == []
    assert ("Order", 15) in matches("What does shop.orders.Order do?")


def test_an_inherited_method_is_found_through_the_subclass_name():
    # finish_order is defined on CheckoutMixin, but the question uses the subclass
    assert matches("How does Store.finish_order work?")[0] == ("CheckoutMixin.finish_order", 30)


def test_self_calls_in_a_mixin_resolve_to_the_real_implementation_not_the_stub():
    found = dict(matches("How does CheckoutMixin.finish_order work?"))

    assert "Store.place_order" in found
    assert "CheckoutMixin.place_order" not in found  # "def place_order(self): ..." is a stub


def test_imported_helpers_are_followed_into_their_module():
    found = find_named_symbols("How does Cart.show_total work?", TABLE, FILES)

    assert ("format_price", "src/shop/utils.py") in [(match.symbol, match.path) for match in found]


def test_calls_inside_nested_functions_are_not_counted():
    callees = get_direct_callees(FILES["src/shop/cart.py"], "Cart.remove_discount_code")

    assert ("secret_helper", True) not in callees


def test_callees_are_plain_calls_and_self_calls():
    assert get_direct_callees(FILES["src/shop/cart.py"], "Cart.update_total") == [("compute_total", False)]
    # self.items.append() is a call on an attribute of self, not on self, so it isn't a helper
    assert get_direct_callees(FILES["src/shop/cart.py"], "Cart.add_item") == [("update_total", True)]
    assert get_direct_callees(FILES["src/shop/cart.py"], "Cart.missing") == []
    assert get_direct_callees("this is not python(", "anything") == []


def test_a_question_without_names_finds_nothing():
    assert matches("What is the production database password?") == []


# ---------------------- Edge cases from the review ------------------------------
def build(files):
    return SymbolTable.build(chunk_repository(files, SHA), files), files


def named(question, table_and_files):
    table, files = table_and_files
    return [(match.symbol, match.path, match.score) for match in find_named_symbols(question, table, files)]


def test_two_classes_with_the_same_name_in_different_files_are_not_mixed_up():
    repository = build({
        "src/app/session.py": "class Session:\n    def send_auth(self):\n        return 1\n",
        "tests/fakes.py": "class FakeBase:\n    def send_fake_auth(self):\n        return 2\n\nclass Session(FakeBase):\n    pass\n",
    })

    found = named("How does Session.send_fake_auth work?", repository)

    # Only the fake Session inherits from FakeBase, so only that file's method matches
    assert found == [("FakeBase.send_fake_auth", "tests/fakes.py", 30)]
    assert named("How does Session.send_auth work?", repository) == [("Session.send_auth", "src/app/session.py", 30)]


def test_a_qualified_name_skips_stubs_and_overridden_parent_methods():
    repository = build({
        "src/base.py": '''class SenderMixin:
    def send(self):
        ...

class BaseSession:
    def send(self):
        return "base"

class FastSession(SenderMixin, BaseSession):
    def send(self):
        return "fast"

class SlowSession(SenderMixin, BaseSession):
    pass
''',
    })

    assert named("How does FastSession.send work?", repository) == [("FastSession.send", "src/base.py", 30)]
    # SlowSession doesn't override send, and the mixin's send is a stub, so BaseSession.send is the real one
    assert named("How does SlowSession.send work?", repository) == [("BaseSession.send", "src/base.py", 30)]


def test_inheritance_cycles_do_not_loop_forever():
    table, _ = build({"src/cycle.py": "class A(B):\n    pass\n\nclass B(A):\n    def run_job(self):\n        return 1\n"})

    assert table.find_method(("A", "src/cycle.py"), "run_job") == ("B.run_job", "src/cycle.py")
    assert {key[0] for key in table.ancestors(("A", "src/cycle.py"))} == {"A", "B"}


def test_absolute_imports_only_match_whole_module_names():
    repository = build({
        "src/requests/utils.py": "def helper_fn():\n    return 1\n",
        "src/myrequests/utils.py": "def helper_fn():\n    return 2\n",
        "src/photos.py": "def path():\n    return 'photo'\n",
        "src/requests/api.py": (
            "from requests.utils import helper_fn\n"
            "from os import path\n\n"
            "def call_helpers():\n    helper_fn()\n    path()\n"
        ),
    })

    found = named("How does call_helpers work?", repository)

    assert ("helper_fn", "src/requests/utils.py", 10) in found
    assert all(path != "src/myrequests/utils.py" for _, path, _ in found)
    # "from os import path" is the standard library, not src/photos.py
    assert all(symbol != "path" for symbol, _, _ in found)


def test_parsing_is_safe_from_several_threads():
    from concurrent.futures import ThreadPoolExecutor

    from app.retrieval.symbols import parse_python

    sources = [f"def function_{number}():\n    return {number}\n" for number in range(300)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        trees = list(pool.map(parse_python, sources * 4))

    assert all(tree is not None for tree in trees)


def test_overload_stubs_do_not_hide_the_real_method():
    repository = build({
        "src/models.py": '''from typing import overload

class Response:
    @overload
    def iter_content(self, size: int) -> bytes: ...
    @overload
    def iter_content(self, size: None) -> str: ...
    def iter_content(self, size=None):
        return self.raw.read(size)
''',
    })

    assert named("How does Response.iter_content work?", repository) == [("Response.iter_content", "src/models.py", 30)]

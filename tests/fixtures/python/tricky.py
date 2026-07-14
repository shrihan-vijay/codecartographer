"""Module docstring."""
import os
from .sibling import helper


def top_level(a: int) -> int:
    """Top level docstring."""

    def nested(b: int) -> int:
        return helper(b) + a

    return nested(a)


class Base:
    pass


class Derived(Base):
    """A derived class."""

    @staticmethod
    def static_method():
        return top_level(1)

    def instance_method(self, x):
        return self.static_method() + x

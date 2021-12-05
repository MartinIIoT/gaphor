"""The code generator for modeling languages.

This is the code generator for the models used by Gaphor.

In order to work with the code generator, a model should follow some conventions:

* `Profile` packages are only for profiles (excluded from generation)
* A stereotype `simpleAttribute` can be defined, which converts an association
  to a `str` attribute
* A stereotype attribute `subsets` can be defined in case an association is derived

The coder first write the class declarations, including attributes and enumerations.
After that, associations are filled in, including derived unions and redefines.

Notes:
* Enumerations are classes ending with "Kind" or "Sort".
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import sys
import textwrap
from pathlib import Path
from typing import Iterable

from gaphor import UML
from gaphor.codegen.override import Overrides
from gaphor.core.modeling import ElementFactory
from gaphor.storage import storage
from gaphor.UML.modelinglanguage import UMLModelingLanguage

log = logging.getLogger(__name__)

header = textwrap.dedent(
    """\
    # This file is generated by coder.py. DO NOT EDIT!
    # {}: skip_file
    # {}: noqa F401,F811
    # fmt: off

    from __future__ import annotations

    from gaphor.core.modeling.properties import (
        association,
        attribute as _attribute,
        derived,
        derivedunion,
        enumeration as _enumeration,
        redefine,
        relation_many,
        relation_one,
    )

    """.format(
        "isort", "flake8"
    )  # work around tooling triggers
)


def main(
    modelfile: str,
    supermodelfiles: list[tuple[str, str]] | None = None,
    overridesfile: str | None = None,
    outfile: str | None = None,
):
    logging.basicConfig()

    model = load_model(modelfile)
    super_models = (
        [(pkg, load_model(f)) for pkg, f in supermodelfiles] if supermodelfiles else []
    )
    overrides = Overrides(overridesfile) if overridesfile else None

    with (open(outfile, "w") if outfile else contextlib.nullcontext(sys.stdout)) as out:
        for line in coder(model, super_models, overrides):
            print(line, file=out)


def coder(
    model: ElementFactory,
    super_models: list[tuple[str, ElementFactory]],
    overrides: Overrides | None,
) -> Iterable[str]:

    already_imported = set()

    classes = list(
        order_classes(
            c
            for c in model.select(UML.Class)
            if not (
                is_enumeration(c)
                or is_simple_type(c)
                or is_in_profile(c)
                or is_tilde_type(c)
            )
        )
    )

    yield header
    if overrides and overrides.header:
        yield overrides.header

    for c in classes:
        if overrides and overrides.has_override(c.name):
            yield overrides.get_override(c.name)
            continue

        super_class = in_super_model(c.name, super_models)
        if super_class:
            pkg, cls = super_class
            line = f"from {pkg} import {cls.name}"
            yield line
            already_imported.add(line)
            continue

        yield class_declaration(c)
        properties = list(variables(c, overrides))
        if properties:
            yield from (f"    {p}" for p in properties)
        else:
            yield "    pass"
        yield ""
        yield ""

    for c in classes:
        yield from operations(c, overrides)

    yield ""

    for c in classes:
        yield from associations(c, overrides)
        for line in subsets(c, super_models):
            if line.startswith("from "):
                if line not in already_imported:
                    yield line
                already_imported.add(line)
            else:
                yield line


def class_declaration(class_: UML.Class):
    base_classes = ", ".join(
        c.name for c in sorted(bases(class_), key=lambda c: c.name)  # type: ignore[no-any-return]
    )
    return f"class {class_.name}({base_classes}):"


def variables(class_: UML.Class, overrides: Overrides | None = None):
    if class_.ownedAttribute:
        for a in sorted(class_.ownedAttribute, key=lambda a: a.name or ""):
            if is_extension_end(a):
                continue

            full_name = f"{class_.name}.{a.name}"
            if overrides and overrides.has_override(full_name):
                yield f"{a.name}: {overrides.get_type(full_name)}"
            elif a.isDerived and not a.type:
                log.warning(f"Derived attribute {full_name} has no implementation.")
            elif a.typeValue:
                yield f'{a.name}: _attribute[{a.typeValue}] = _attribute("{a.name}", {a.typeValue}{default_value(a)})'
            elif is_enumeration(a.type):
                enum_values = ", ".join(f'"{e.name}"' for e in a.type.ownedAttribute)
                yield f'{a.name} = _enumeration("{a.name}", ({enum_values}), "{a.type.ownedAttribute[0].name}")'
            elif a.type:
                mult = "one" if a.upper == "1" else "many"
                comment = "  # type: ignore[assignment]" if is_reassignment(a) else ""
                yield f"{a.name}: relation_{mult}[{a.type.name}]{comment}"
            else:
                raise ValueError(
                    f"{a.name}: {a.type} can not be written; owner={a.owner.name}"
                )

    if class_.ownedOperation:
        for o in sorted(class_.ownedOperation, key=lambda a: a.name or ""):
            full_name = f"{class_.name}.{o.name}"
            if overrides and overrides.has_override(full_name):
                yield f"{o.name}: {overrides.get_type(full_name)}"
            else:
                log.warning(f"Operation {full_name} has no implementation")


def associations(
    c: UML.Class,
    overrides: Overrides | None = None,
):
    redefinitions = []
    for a in c.ownedAttribute:
        full_name = f"{c.name}.{a.name}"
        if overrides and overrides.has_override(full_name):
            yield overrides.get_override(full_name)
        elif (
            not a.type
            or is_simple_type(a.type)
            or is_enumeration(a.type)
            or is_extension_end(a)
        ):
            continue
        elif redefines(a):
            redefinitions.append(
                f'{full_name} = redefine({c.name}, "{a.name}", {a.type.name}, {redefines(a)})'
            )
        elif a.isDerived:
            yield f'{full_name} = derivedunion("{a.name}", {a.type.name}{lower(a)}{upper(a)})'
        elif not a.name:
            raise ValueError(f"Unnamed attribute: {full_name} ({a.association})")
        else:
            yield f'{full_name} = association("{a.name}", {a.type.name}{lower(a)}{upper(a)}{composite(a)}{opposite(a)})'

    yield from redefinitions


def subsets(
    c: UML.Class,
    super_models: list[tuple[str, ElementFactory]],
):
    for a in c.ownedAttribute:
        if (
            not a.type
            or is_simple_type(a.type)
            or is_enumeration(a.type)
            or is_extension_end(a)
        ):
            continue
        for slot in a.appliedStereotype[:].slot:
            if slot.definingFeature.name == "subsets":
                full_name = f"{c.name}.{a.name}"
                for value in slot.value.split(","):
                    pkg, d = attribute(c, value.strip(), super_models)
                    if d and d.isDerived:
                        if pkg:
                            yield f"from {pkg} import {d.owner.name}"  # type: ignore[attr-defined]
                        yield f"{d.owner.name}.{d.name}.add({full_name})  # type: ignore[attr-defined]"  # type: ignore[attr-defined]
                    elif not d:
                        log.warning(
                            f"{full_name} wants to subset {value.strip()}, but it is not defined"
                        )
                    else:
                        log.warning(
                            f"{full_name} wants to subset {value.strip()}, but it is not a derived union"
                        )


def operations(c: UML.Class, overrides: Overrides | None = None):
    if c.ownedOperation:
        for o in sorted(c.ownedOperation, key=lambda a: a.name or ""):
            full_name = f"{c.name}.{o.name}"
            if overrides and overrides.has_override(full_name):
                yield overrides.get_override(full_name)


def default_value(a):
    if a.defaultValue:
        if a.typeValue == "int":
            defaultValue = a.defaultValue.title()
        elif a.typeValue == "str":
            defaultValue = f'"{a.defaultValue}"'
        else:
            raise ValueError(
                f"Unknown default value type: {a.owner.name}.{a.name}: {a.typeValue} = {a.defaultValue}"
            )

        return f", default={defaultValue}"
    return ""


def lower(a):
    return "" if a.lowerValue in (None, "0") else f", lower={a.lowerValue}"


def upper(a):
    return "" if a.upperValue in (None, "*") else f", upper={a.upperValue}"


def composite(a):
    return ", composite=True" if a.aggregation == "composite" else ""


def opposite(a):
    return (
        f', opposite="{a.opposite.name}"'
        if a.opposite and a.opposite.name and a.opposite.class_
        else ""
    )


def order_classes(classes: Iterable[UML.Class]) -> Iterable[UML.Class]:
    seen_classes = set()

    def order(c):
        if c not in seen_classes:
            for b in bases(c):
                yield from order(b)
            yield c
            seen_classes.add(c)

    for c in classes:
        yield from order(c)


def bases(c: UML.Class) -> Iterable[UML.Class]:
    for g in c.generalization:
        yield g.general

    for a in c.ownedAttribute:
        if a.association and a.name == "baseClass":
            meta_cls = a.association.ownedEnd.class_
            yield meta_cls


def is_enumeration(c: UML.Class) -> bool:
    return c and c.name and (c.name.endswith("Kind") or c.name.endswith("Sort"))  # type: ignore[return-value]


def is_simple_type(c: UML.Class) -> bool:
    return any(
        s.name == "SimpleAttribute" for s in UML.model.get_applied_stereotypes(c)
    ) or any(is_simple_type(g.general) for g in c.generalization)


def is_tilde_type(c: UML.Class) -> bool:
    return c and c.name and c.name.startswith("~")  # type: ignore[return-value]


def is_extension_end(a: UML.Property):
    return isinstance(a.association, UML.Extension)


def is_reassignment(a: UML.Property) -> bool:
    def test(c: UML.Class):
        for attr in c.ownedAttribute:
            if attr.name == a.name:
                return True
        return any(test(base) for base in bases(c))

    return any(test(base) for base in bases(a.owner))  # type:ignore[arg-type]


def is_in_profile(c: UML.Class) -> bool:
    def test(p: UML.Package):
        return isinstance(p, UML.Profile) or (p.owningPackage and test(p.owningPackage))

    return test(c.owningPackage)  # type: ignore[no-any-return]


def is_in_toplevel_package(c: UML.Class, package_name: str) -> bool:
    def test(p: UML.Package):
        return (not p.owningPackage and p.name == package_name) or (
            p.owningPackage and test(p.owningPackage)
        )

    return test(c.owningPackage)  # type: ignore[no-any-return]


def redefines(a: UML.Property) -> str | None:
    slot: UML.Slot
    for slot in a.appliedStereotype[:].slot:
        if slot.definingFeature.name == "redefines":
            return slot.value  # type: ignore[no-any-return]
    return None


def attribute(
    c: UML.Class, name: str, super_models: list[tuple[str, ElementFactory]]
) -> tuple[str | None, UML.Property | None]:
    for a in c.ownedAttribute:
        if a.name == name:
            return None, a

    for base in bases(c):
        pkg, a = attribute(base, name, super_models)
        if a:
            return pkg, a

    maybe_super = in_super_model(c.name, super_models)
    if maybe_super:
        return maybe_super[0], attribute(maybe_super[1], name, [])[1]

    return None, None


def in_super_model(
    name: str, super_models: list[tuple[str, ElementFactory]]
) -> tuple[str, UML.Class] | None:
    for pkg, factory in super_models:
        cls: UML.Class
        for cls in factory.select(  # type: ignore[assignment]
            lambda e: isinstance(e, UML.Class) and e.name == name
        ):
            if not (is_in_profile(cls) or is_enumeration(cls)):
                return pkg, cls
    return None


def load_model(modelfile: str) -> ElementFactory:
    element_factory = ElementFactory()
    uml_modeling_language = UMLModelingLanguage()
    storage.load(
        modelfile,
        element_factory,
        uml_modeling_language,
    )

    resolve_attribute_type_values(element_factory)

    return element_factory


def resolve_attribute_type_values(element_factory: ElementFactory) -> None:
    """Some model updates that are hard to do from Gaphor itself."""
    for prop in element_factory.select(UML.Property):
        if prop.typeValue in ("String", "str", "object"):
            prop.typeValue = "str"
        elif prop.typeValue in (
            "Integer",
            "int",
            "Boolean",
            "bool",
            "UnlimitedNatural",
        ):
            prop.typeValue = "int"
        else:
            c: UML.Class | None = next(
                element_factory.select(
                    lambda e: isinstance(e, UML.Class) and e.name == prop.typeValue
                ),  # type: ignore[arg-type]
                None,
            )
            if c:
                prop.type = c
                del prop.typeValue

        if prop.type and is_simple_type(prop.type):  # type: ignore[arg-type]
            prop.typeValue = "str"
            del prop.type

        if not (prop.type or prop.typeValue in ("str", "int", None)):
            raise ValueError(f"Property value type {prop.typeValue} can not be found")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("modelfile", type=Path, help="Gaphor model filename")
    parser.add_argument(
        "-o", dest="outfile", type=Path, help="Python data model filename"
    )
    parser.add_argument("-r", dest="overridesfile", type=Path, help="Override filename")
    parser.add_argument(
        "-s",
        dest="supermodelfiles",
        type=str,
        action="append",
        help="Reference to dependent model file (e.g. gaphor.UML.uml:models/UML.gaphor)",
    )

    args = parser.parse_args()
    supermodelfiles = (
        [s.split(":") for s in args.supermodelfiles] if args.supermodelfiles else []
    )

    main(args.modelfile, supermodelfiles, args.overridesfile, args.outfile)

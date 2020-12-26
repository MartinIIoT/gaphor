from __future__ import annotations

import ast
from dataclasses import replace

import gaphas
from gaphas.aspect.connector import ConnectionSink
from gaphas.aspect.connector import Connector as ConnectorAspect
from gaphas.connector import Handle
from gaphas.geometry import Rectangle, distance_rectangle_point
from gaphas.item import matrix_i2i

from gaphor.core.modeling.diagram import Diagram
from gaphor.core.modeling.presentation import Presentation, S
from gaphor.core.styling import Style
from gaphor.diagram.shapes import combined_style
from gaphor.diagram.text import TextAlign, text_point_at_line


class Named:
    """Marker for any NamedElement presentations."""


class Classified(Named):
    """Marker for Classifier presentations."""


def from_package_str(item):
    """Display name space info when it is different, then diagram's or parent's
    namespace."""
    subject = item.subject
    diagram = item.diagram

    if not (subject and diagram):
        return False

    namespace = subject.namespace
    parent = item.parent

    # if there is a parent (i.e. interaction)
    if parent and parent.subject and parent.subject.namespace is not namespace:
        return False

    return f"(from {namespace.name})" if namespace is not item.diagram.namespace else ""


def _get_sink(item, handle, target):
    assert item.diagram

    hpos = matrix_i2i(item, target).transform_point(*handle.pos)
    port = None
    dist = 10e6
    for p in target.ports():
        pos, d = p.glue(hpos)
        if not port or d < dist:
            port = p
            dist = d

    return ConnectionSink(target, port)


def postload_connect(item: gaphas.Item, handle: gaphas.Handle, target: gaphas.Item):
    """Helper function: when loading a model, handles should be connected as
    part of the `postload` step.

    This function finds a suitable spot on the `target` item to connect
    the handle to.
    """
    connector = ConnectorAspect(item, handle, item.diagram.connections)
    sink = _get_sink(item, handle, target)
    connector.connect(sink)


# Note: the official documentation is using the terms "Shape" and "Edge" for element and line.


class ElementPresentation(gaphas.Element, Presentation[S]):
    """Presentation for Gaphas Element (box-like) items.

    To create a shape (boxes, text), assign a shape to `self.shape`. If
    the shape can change, for example, because styling needs to change,
    implement the method `update_shapes()` and set self.shape there.
    """

    width: int
    height: int

    _port_sides = ("top", "right", "bottom", "left")

    def __init__(self, diagram: Diagram, id=None, shape=None):
        super().__init__(connections=diagram.connections, diagram=diagram, id=id)  # type: ignore[call-arg]
        self._shape = shape

    def port_side(self, port):
        return self._port_sides[self._ports.index(port)]

    def _set_shape(self, shape):
        self._shape = shape
        self.request_update()

    shape = property(lambda s: s._shape, _set_shape)

    def update_shapes(self, event=None):
        """Updating the shape configuration, e.g. when extra elements have to
        be drawn or when styling changes."""

    def pre_update(self, context):
        self.min_width, self.min_height = self.shape.size(context)

    def post_update(self, context):
        pass

    def draw(self, context):
        x, y = self.handles()[0].pos
        cairo = context.cairo
        cairo.translate(x, y)
        self._shape.draw(
            context,
            Rectangle(0, 0, self.width, self.height),
        )

    def save(self, save_func):
        save_func("matrix", tuple(self.matrix))
        for prop in ("width", "height"):
            save_func(prop, getattr(self, prop))
        super().save(save_func)

    def load(self, name, value):
        if name == "matrix":
            self.matrix.set(*ast.literal_eval(value))
        elif name in ("width", "height"):
            setattr(self, name, ast.literal_eval(value))
        else:
            super().load(name, value)

    def postload(self):
        super().postload()
        self.update_shapes()


class LinePresentation(gaphas.Line, Presentation[S]):
    def __init__(
        self,
        diagram: Diagram,
        id=None,
        style: Style = {},
        shape_head=None,
        shape_middle=None,
        shape_tail=None,
    ):
        super().__init__(connections=diagram.connections, diagram=diagram, id=id)  # type: ignore[call-arg]

        self.style = style
        self.shape_head = shape_head
        self.shape_middle = shape_middle
        self.shape_tail = shape_tail

        self.fuzziness = 2
        self._shape_head_rect = None
        self._shape_middle_rect = None
        self._shape_tail_rect = None

    head = property(lambda self: self._handles[0])
    tail = property(lambda self: self._handles[-1])

    def pre_update(self, context):
        pass

    def post_update(self, context):
        def shape_bounds(shape, align):
            if shape:
                size = shape.size(context)
                x, y = text_point_at_line(points, size, align)
                return Rectangle(x, y, *size)

        points = [h.pos for h in self.handles()]
        self._shape_head_rect = shape_bounds(self.shape_head, TextAlign.LEFT)
        self._shape_middle_rect = shape_bounds(self.shape_middle, TextAlign.CENTER)
        self._shape_tail_rect = shape_bounds(self.shape_tail, TextAlign.RIGHT)

    def point(self, x, y):
        """Given a point (x, y) return the distance to the diagram item."""
        d0 = super().point(x, y)
        ds = [
            distance_rectangle_point(shape, (x, y))
            for shape in (
                self._shape_head_rect,
                self._shape_middle_rect,
                self._shape_tail_rect,
            )
            if shape
        ]
        return min(d0, *ds) if ds else d0

    def draw(self, context):
        style = combined_style(context.style, self.style)
        context = replace(context, style=style)

        cr = context.cairo
        self.line_width = style["line-width"]
        cr.set_dash(style.get("dash-style", ()), 0)
        stroke = style["color"]
        if stroke:
            cr.set_source_rgba(*stroke)

        super().draw(context)

        for shape, rect in (
            (self.shape_head, self._shape_head_rect),
            (self.shape_middle, self._shape_middle_rect),
            (self.shape_tail, self._shape_tail_rect),
        ):
            if shape:
                shape.draw(context, rect)

    def save(self, save_func):
        def save_connection(name, handle):
            c = self._connections.get_connection(handle)
            if c:
                save_func(name, c.connected)

        super().save(save_func)
        save_func("matrix", tuple(self.matrix))
        for prop in ("orthogonal", "horizontal"):
            save_func(prop, getattr(self, prop))
        points = [tuple(map(float, h.pos)) for h in self.handles()]
        save_func("points", points)

        save_connection("head-connection", self.head)
        save_connection("tail-connection", self.tail)

    def load(self, name, value):
        if name == "matrix":
            self.matrix.set(*ast.literal_eval(value))
        elif name == "points":
            points = ast.literal_eval(value)
            for _ in range(len(points) - 2):
                h = Handle((0, 0))
                self._handles.insert(1, h)
            for i, p in enumerate(points):
                self.handles()[i].pos = p

            # Update connection ports of the line. Only handles are saved
            # in Gaphor file therefore ports need to be recreated after
            # handles information is loaded.
            self._update_ports()

        elif name == "orthogonal":
            self._load_orthogonal = ast.literal_eval(value)
        elif name == "horizontal":
            self.horizontal = ast.literal_eval(value)
        elif name in ("head_connection", "head-connection"):
            self._load_head_connection = value
        elif name in ("tail_connection", "tail-connection"):
            self._load_tail_connection = value
        else:
            super().load(name, value)

    def postload(self):
        if hasattr(self, "_load_orthogonal"):
            # Ensure there are enough handles
            if self._load_orthogonal and len(self._handles) < 3:
                p0 = self._handles[-1].pos
                self._handles.insert(1, self._create_handle(p0))
            self.orthogonal = self._load_orthogonal
            del self._load_orthogonal

        if hasattr(self, "_load_head_connection"):
            postload_connect(self, self.head, self._load_head_connection)
            del self._load_head_connection

        if hasattr(self, "_load_tail_connection"):
            postload_connect(self, self.tail, self._load_tail_connection)
            del self._load_tail_connection

        super().postload()

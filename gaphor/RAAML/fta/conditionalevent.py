"""Conditional Event item definition."""

from gaphas.geometry import Rectangle
from gaphas.util import path_ellipse

from gaphor.core.modeling import DrawContext
from gaphor.core.styling.properties import VerticalAlign
from gaphor.diagram.presentation import (
    Classified,
    ElementPresentation,
    from_package_str,
)
from gaphor.diagram.shapes import Box, Text, stroke
from gaphor.diagram.support import represents
from gaphor.diagram.text import FontStyle, FontWeight
from gaphor.RAAML import raaml
from gaphor.UML.modelfactory import stereotypes_str


@represents(raaml.ConditionalEvent)
class ConditionalEventItem(ElementPresentation, Classified):
    def __init__(self, diagram, id=None):
        super().__init__(diagram, id)

        self.watch("subject[NamedElement].name").watch(
            "subject[NamedElement].namespace.name"
        )

    def update_shapes(self, event=None):
        self.shape = Box(
            Box(
                Text(
                    text=lambda: stereotypes_str(self.subject, ["ConditionalEvent"]),
                ),
                Text(
                    text=lambda: self.subject.name or "",
                    width=lambda: self.width - 4,
                    style={
                        "font-weight": FontWeight.BOLD,
                        "font-style": FontStyle.NORMAL,
                    },
                ),
                Text(
                    text=lambda: from_package_str(self),
                    style={"font-size": "x-small"},
                ),
                style={
                    "padding": (55, 4, 0, 4),
                    "min-height": 100,
                },
            ),
            style={"vertical-align": VerticalAlign.BOTTOM},
            draw=draw_conditional_event,
        )


def draw_conditional_event(box, context: DrawContext, bounding_box: Rectangle):
    cr = context.cairo

    rx = bounding_box.width - 40
    ry = bounding_box.height - 50

    cr.move_to(bounding_box.width, ry)
    path_ellipse(cr, bounding_box.width / 2.0, ry / 2.0, rx, ry)
    stroke(context)

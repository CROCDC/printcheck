"""The figure of what was judged: written, cut where asked, and skipped
without matplotlib."""

import builtins

from printcheck import views

from helpers import box, part


def test_cut_both_axes():
    m = box(40, 20, 10, x=5)
    across_y = views.cut(m, "y", 0.0)
    assert across_y is not None
    # cut across y keeps the part's own x: the 40 mm span centred on x = 5
    x0, z0, x1, z1 = across_y.bounds
    assert round(x0, 3) == -15 and round(x1, 3) == 25 and round(z1, 3) == 10
    across_x = views.cut(m, "x", 5.0)
    y0, _, y1, _ = across_x.bounds
    assert round(y0, 3) == -10 and round(y1, 3) == 10
    assert views.cut(m, "y", 500.0) is None
    assert views.cut(m, "x", 500.0) is None


def test_render_default_cuts(tmp_path):
    body, inlay = box(40, 20, 6), box(10, 4, 1, z=5)
    path = views.render([part(body, "body"), part(inlay, "inlay", 2)], str(tmp_path / "sub" / "fig.png"))
    assert path.endswith("fig.png") and (tmp_path / "sub" / "fig.png").stat().st_size > 1000


def test_render_custom_cuts_and_empty_planes(tmp_path):
    # the first plane crosses the body but not the companion; the second misses both
    body, inlay = box(40, 20, 6), box(4, 4, 1, x=15, z=5)
    path = views.render([part(body, "body"), part(inlay, "inlay", 2)], str(tmp_path / "fig.png"),
                        cuts=[("y", 9.0), ("x", 400.0)])
    assert path and (tmp_path / "fig.png").exists()


def test_render_without_matplotlib(monkeypatch, tmp_path):
    real = builtins.__import__

    def no_mpl(name, *args, **kw):
        if name.startswith("matplotlib"):
            raise ImportError(name)
        return real(name, *args, **kw)
    monkeypatch.setattr(builtins, "__import__", no_mpl)
    assert views.render([part(box(5, 5, 5))], str(tmp_path / "x.png")) is None
    assert not (tmp_path / "x.png").exists()

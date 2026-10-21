def led_rgb(r, g, b):
    """0-255 ints; floats all at most 1 are fractions (CrazyflieSimLogger.setLEDColor's convention)"""
    values = (r, g, b)
    if all(isinstance(v, float) and v <= 1.0 for v in values):
        values = tuple(v * 255 for v in values)
    return tuple(int(min(max(round(v), 0), 255)) for v in values)

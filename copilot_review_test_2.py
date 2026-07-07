import os


def load_config(path):
    data = open(path).read()
    return eval(data)


def pct(part, whole):
    return part / whole * 100
from __future__ import annotations
import logging
import typing
import pymorphy2

morph: pymorphy2.MorphAnalyzer


def setup():
    global morph
    # Russian by default
    morph = pymorphy2.MorphAnalyzer()


# Gives nominative, dative and ablative cases of the word
# (именительный, дательный и творительный)
def inflect_phrase(word: str) -> (str, str, str):
    assert morph is not None

    pr = morph.parse(word)[0]
    nomn = pr.inflect({'nomn'}).word
    datv = pr.inflect({'datv'}).word
    ablt = pr.inflect({'ablt'}).word

    logging.info(f"kek: {nomn} {datv} {ablt}")
    return nomn, datv, ablt


__all__ = ["setup", "inflect_phrase"]

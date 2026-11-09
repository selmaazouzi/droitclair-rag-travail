from ingestion.html_text import html_to_paragraphs


def test_paragraph_blocks() -> None:
    html = "<p>Premier alinéa : </p><p> 1° Pour les cadres, de quatre mois. </p><p></p>"
    assert html_to_paragraphs(html) == ["Premier alinéa :", "1° Pour les cadres, de quatre mois."]


def test_double_br_inside_paragraph() -> None:
    html = "<p>Montants suivants :<br/><br/>\n 1° Un quart ;<br/><br/>\n 2° Un tiers.</p>"
    assert html_to_paragraphs(html) == ["Montants suivants :", "1° Un quart ;", "2° Un tiers."]


def test_inline_link_and_sup_are_kept_as_text() -> None:
    html = '<p>Voir l\'<a href="/x" title="t">article L. 1242-3</a> du livre I<sup>er</sup>.</p>'
    assert html_to_paragraphs(html) == ["Voir l'article L. 1242-3 du livre Ier."]


def test_missing_space_after_link_is_restored() -> None:
    html = (
        '<p>en application de l\'article <a href="/x">L. 1233-87</a>procède à un licenciement</p>'
    )
    assert html_to_paragraphs(html) == [
        "en application de l'article L. 1233-87 procède à un licenciement"
    ]


def test_link_followed_by_punctuation_gets_no_space() -> None:
    assert html_to_paragraphs('<p>article <a href="/x">L. 1242-3</a>.</p>') == [
        "article L. 1242-3."
    ]


def test_table_rendered_one_row_per_line() -> None:
    # Shape of the real L1235-3 markup (dismissal compensation scale).
    html = (
        '<p>Montants fixés dans le tableau ci-dessous. </p><center><table border="1"><tbody>'
        "<tr><th><br/>Ancienneté <p></p><p>(en années complètes) </p></th>"
        "<th><br/>Indemnité minimale <p></p><p>(en mois de salaire brut) </p></th></tr>"
        '<tr><td align="center"><br/>0 </td><td align="center"><br/>Sans objet </td></tr>'
        '<tr><td align="center"><br/>2 </td><td align="center"><br/>3,5 </td></tr>'
        "</tbody></table></center><p>Alinéa suivant.</p>"
    )
    assert html_to_paragraphs(html) == [
        "Montants fixés dans le tableau ci-dessous.",
        "Ancienneté (en années complètes) | Indemnité minimale (en mois de salaire brut)",
        "0 | Sans objet",
        "2 | 3,5",
        "Alinéa suivant.",
    ]


def test_entities_and_whitespace() -> None:
    assert html_to_paragraphs("<p>L&apos;employeur\n   informe&nbsp;le salarié</p>") == [
        "L'employeur informe le salarié"
    ]


def test_empty_markup() -> None:
    assert html_to_paragraphs('<p></p><p><br clear="none" /></p>') == []
    assert html_to_paragraphs("") == []

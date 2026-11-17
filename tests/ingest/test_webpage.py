from telltale.ingest.webpage import page_blocks, page_passages

BODY = """
<h2 id="summary">Completed preliminary assessment summary 1/26: Test Vessel</h2>
<h3>Synopsis</h3>
<p>At 0848 the general cargo vessel Test Vessel ran aground.
There was no damage &amp; no one was injured.</p>
<figure class="image embedded"><div class="img"><img src="https://example.org/fig1.png" alt="Test Vessel Figure 1"></div><figcaption>Figure 1</figcaption></figure>
<p>The MAIB's preliminary assessment identified that:</p>
<ul>
  <li><p>the pilot over-corrected the heading</p></li>
  <li><p>the bridge team had no set roles.</p></li>
</ul>
<table>
  <thead><tr><td></td><th scope="col">Ship particulars</th></tr></thead>
  <tbody>
    <tr><th scope="row">Flag</th><td>Testland</td></tr>
    <tr><th scope="row">Place on board</th><td>Hull</td></tr>
  </tbody>
</table>
"""


def test_blocks_follow_paragraphs_lists_and_table_rows():
    assert page_blocks(BODY) == [
        "Completed preliminary assessment summary 1/26: Test Vessel",
        "Synopsis",
        "At 0848 the general cargo vessel Test Vessel ran aground. "
        "There was no damage & no one was injured.",
        "The MAIB's preliminary assessment identified that:",
        "the pilot over-corrected the heading",
        "the bridge team had no set roles.",
        "Ship particulars",
        "Flag: Testland",
        "Place on board: Hull",
    ]


def test_figures_are_left_out():
    text = " ".join(page_blocks(BODY))
    assert "Figure 1" not in text
    assert "fig1.png" not in text


def test_a_list_item_never_runs_into_the_next():
    passages = page_passages(BODY, max_chars=60)
    assert "the pilot over-corrected the heading" in passages


def test_passages_keep_every_sentence_in_order():
    passages = page_passages(BODY)
    joined = " ".join(passages)
    assert joined.startswith("Completed preliminary assessment summary 1/26: Test Vessel Synopsis")
    assert joined.endswith("Flag: Testland Place on board: Hull")
    assert all(len(p) <= 220 for p in passages)


def test_empty_page_gives_no_passages():
    assert page_passages("") == []
    assert page_passages("<figure><img alt='only a picture'></figure>") == []

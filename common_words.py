"""Common English words that never need to be Whisper hotwords.

Whisper already recognises everyday vocabulary, so spending the small hotword
budget on these words only crowds out the names, acronyms and specialist terms
that it actually mishears. Slide-template boilerplate words are included too.
"""

COMMON_WORDS = frozenset(
    """
    a about above according across act action actually add added after again against age ago
    agree agreed agreement ahead all allow allowed almost alone along already also although
    always am among amount an analysis and another answer any anyone anything apply applied
    approach are area areas around as ask asked aspect aspects at available away back based
    basic be became because become been before began begin behind being believe below best
    better between beyond big both bring brought build built business but by call called came
    can cannot case cases cause certain change changes chapter check choose clear click close
    come comes common company complete concept concepts consider considered contact content
    context continue control cost could country course cover create created current data day
    days deal decide decision define defined definition delete describe described detail
    details did different do does doing done down due during each early easy effect either
    else end enough ensure even every everyone everything evidence example examples except
    exist expect experience explain fact factor factors fail far feature features few field
    figure final find first follow following for form found four free from full further
    general get give given go goes going good got great group had happen happened has have
    having he head hear help her here high him his history how however human idea ideas if
    important in include included including increase information insert instead into involve
    involved is issue issues it its itself just keep key kind know known large last later lead
    learn least leave left less let level life like likely line list little live long look
    lot made main make makes making many matter may me mean means member members might more
    most move much must my name need needs never new next no not note nothing now number of
    off often on once one only open or order other others our out outcome over overview own
    page part particular parties party people per perhaps period person place point points
    possible power practice present problem process provide provided public purpose put
    question questions quite rather read real really reason recent refer related relevant
    remove required requirement requirements result results right role rule rules said same
    say says second section see seen set several shall she should show side significant
    similar simple since slide slides small so some something sometimes source specific
    stage start state statement step still structure student students study such summary
    support sure system table take taken team tell term terms than that the their them then
    theory there these they thing things think third this those though three through time
    times title to today together too topic total towards true try turn two type types under
    understand understanding unit until up upon us use used useful uses using usually value
    various very view want was way ways we week well were what when where whether which while
    who whole whom why will with within without word words work works would write written
    year years yes yet you your
    """.split()
)

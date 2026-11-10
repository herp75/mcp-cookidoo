"""The weekly editor check (tests/live/editor_check.py), offline, on a made-up script."""

from dataclasses import replace

from devices import BUILTIN_CATALOG
from tests.live import editor_check

# The shape of the editor script's mode classes (minified), with the limits of devices.py
SCRIPT = "".join(
    [
        "showTimeSection(e){return this.timeSection.show(e,{required:!0,minTime:1,"
        "maxTime:5940,defaultTime:0})}",
        'enable(e={}){this.modeSection.show({mode:"dough"}),this.timeSection.show(e,'
        "{minTime:1,maxTime:1200})}disable(){}",
        'enable(e={}){this.modeSection.show({mode:"blend"}),this.timeSection.show(e,'
        '{minTime:10,maxTime:300}),this.speedSection.show(e,{availableSpeeds:["6","6.5","7",'
        '"7.5","8"]})}disable(){}',
        'enable(e={}){this.modeSection.show({mode:"warm_up"}),this.temperatureSection.show(e,'
        '{availableTemperatures:this.temperatureSection.unit==="C"?["37","40","45","50","55",'
        '"60","65","70","75","80","85","90"]:["100","105"]}),this.speedSection.show(e,'
        '{availableSpeeds:["soft","1","2"]})}disable(){}',
        'enable(e={}){this.modeSection.show({mode:"steaming"}),this.timeSection.show(e,'
        '{minTime:1,maxTime:5940}),this.speedSection.show(e,{availableSpeeds:["soft"]})}disable(){}',
        'enable(e={}){this.modeSection.show({mode:"browning"}),this.timeSection.show(e,'
        "{minTime:1,maxTime:1800,defaultTime:600})}disable(){}",
        'enable(e={}){this.modeSection.show({mode:"turbo"}),this.timeSection.show(e,'
        "{useSelect:!0,defaultTime:1})}disable(){}",
    ]
)
PAGE = (
    '<link href="https://patternlib-all.prod.external.eu-tm-prod.vorwerk-digital.com/'
    'pl-customer-recipes-1.174.15-d24afd988b9d4b5b1a4cef21d71582ec.css">'
    '<script src="https://patternlib-all.prod.external.eu-tm-prod.vorwerk-digital.com/'
    'pl-customer-recipes-1.174.15-bedf0d65c4ea041a79c2f3b11b39b345.js"></script>'
)


def test_matching_script_has_no_differences():
    found = editor_check.script_limits(SCRIPT)
    assert set(found) == {"manual", "dough", "blend", "warm_up", "steaming", "browning", "turbo"}
    assert editor_check.compare_script(found) == []


def test_changed_limits_are_reported():
    changed = SCRIPT.replace("maxTime:1200", "maxTime:1500").replace(
        'availableSpeeds:["soft","1","2"]', 'availableSpeeds:["soft","1","2","3"]'
    )
    assert editor_check.compare_script(editor_check.script_limits(changed)) == [
        "`dough` time: editor (1, 1500), devices.py (1, 1200)",
        "`warm_up` speeds: editor ('soft', '1', '2', '3'), devices.py ('soft', '1', '2')",
    ]


def test_missing_modes_are_reported():
    differences = editor_check.compare_script(editor_check.script_limits("nothing here"))
    assert "`dough`: not found in the editor script" in differences
    assert "`manual`: not found in the editor script" in differences
    assert not any("turbo" in d or "rice_cooker" in d for d in differences)


def test_script_url_only_from_the_pattern_library_host():
    found = editor_check.script_url(PAGE)
    assert found is not None
    url, version = found
    assert url.endswith(".js") and version == "1.174.15"
    assert editor_check.script_url(PAGE.replace("patternlib-all.prod", "evil.example")) is None


def test_catalog_differences_and_report():
    live = replace(BUILTIN_CATALOG, tm7_only=frozenset({"browning"}), pulse_counts=(1, 2))
    differences = editor_check.compare_catalog(live)
    assert differences == [
        "editor page `tm7_only`: ['browning'], built-in ['browning', 'steaming']",
        "editor page `pulse_counts`: [1, 2], built-in [1, 2, 3, 4, 5, 6, 7, 8, 9]",
    ]
    assert editor_check.compare_catalog(BUILTIN_CATALOG) == []
    report = editor_check.Report("https://cookidoo.ch/foundation/de-CH", "1.174.15", differences)
    text = report.markdown()
    assert "pl-customer-recipes 1.174.15" in text and "- editor page `tm7_only`" in text

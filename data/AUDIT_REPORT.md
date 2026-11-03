# DATA_V2 AUDIT REPORT

Source: CRONQUESTIONS data_v2 (wikidata_big). Read-only audit; no data files modified.

## 1. KG splits
### full.txt: 328635 events, 125726 entities, 203 relations, 0 malformed, start>end: 652, years 1..9500
### train: 323635 events, 124981 entities, 203 relations, 0 malformed, start>end: 647, years 1..9500
### valid: 5000 events, 6865 entities, 89 relations, 0 malformed, start>end: 9, years 25..7700
### test: 5000 events, 6850 entities, 80 relations, 0 malformed, start>end: 5, years 49..2200

Overlap train|valid=5000, train|test=34, valid|test=0. Not-in-full: train=0, valid=0, test=0. Partition==full: False

## 2. Questions
### test.pickle: 30000 records, 18050 unique ids (8197 dup ids), empty answers=0, entities not in KG=0, relations not in KG=[], times 367..2019 (338 distinct)
types: {'time_join': 3832, 'first_last': 11159, 'before_after': 2151, 'simple_time': 5046, 'simple_entity': 7812}
### train.pickle: 350000 records, 183280 unique ids (101316 dup ids), empty answers=0, entities not in KG=0, relations not in KG=[], times 34..2021 (858 distinct)
types: {'time_join': 55453, 'first_last': 118556, 'simple_entity': 90651, 'simple_time': 61471, 'before_after': 23869}
### valid.pickle: 30000 records, 17888 unique ids (8225 dup ids), empty answers=0, entities not in KG=0, relations not in KG=[], times 372..2019 (350 distinct)
types: {'first_last': 11198, 'simple_entity': 7745, 'time_join': 3878, 'simple_time': 5197, 'before_after': 1982}

## 3. Duplicate-id structure
test: 8197 multi-record ids (sampled 2000): identical text=7, paraphrase text=1993, differing answers=0
train: 101316 multi-record ids (sampled 2000): identical text=7, paraphrase text=1993, differing answers=0
valid: 8225 multi-record ids (sampled 2000): identical text=6, paraphrase text=1994, differing answers=0

## 4. Text maps
entities: {'n_entries': 125726, 'kg_entities_missing_text': 0}, relations: {'n_entries': 203, 'kg_relations_missing_text': []}

## 5. tkbc_processed_data
{
  "exists": true,
  "files": {
    "ent_id": {
      "n_lines": 14056,
      "head": [
        "�\u0003}q\u0000(X\u0004\u0000\u0000\u0000Q100q\u0001K\u0000X\u0005\u0000\u0000\u0000Q1000q\u0002K\u0001X\b\u0000\u0000\u0000Q1000051q\u0003K\u0002X\b\u0000\u0000\u0000Q1000061q\u0004K\u0003X\u0006\u0000\u0000\u0000Q10001q\u0005K\u0004X\b\u0000\u0000\u0000Q1000104q\u0006K\u0005X\b\u0000\u0000\u0000Q1000128q\u0007K\u0006X\b\u0000\u0000\u0000Q1000164q\bK\u0007X\u0006\u0000\u0000\u0000Q10002q\tK\bX\u0007\u0000\u0000\u0000Q100028q",
        "K\tX\b\u0000\u0000\u0000Q1000314q",
        "K"
      ]
    },
    "rel_id": {
      "n_lines": 15,
      "head": [
        "�\u0003}q\u0000(X\u0005\u0000\u0000\u0000P1001q\u0001K\u0000X\u0004\u0000\u0000\u0000P101q\u0002K\u0001X\u0004\u0000\u0000\u0000P102q\u0003K\u0002X\u0005\u0000\u0000\u0000P1027q\u0004K\u0003X\u0005\u0000\u0000\u0000P1029q\u0005K\u0004X\u0005\u0000\u0000\u0000P1035q\u0006K\u0005X\u0005\u0000\u0000\u0000P1037q\u0007K\u0006X\u0005\u0000\u0000\u0000P1038q\bK\u0007X\u0005\u0000\u0000\u0000P1050q\tK\bX\u0004\u0000\u0000\u0000P106q",
        "K\tX\u0005\u0000\u0000\u0000P1066q",
        "K"
      ]
    },
    "ts_id": {
      "n_lines": 6172,
      "head": [
        "�\u0003}q\u0000(K\u0000K\u0000K\u0000�q\u0001K\u0000K\u0001K\u0000K\u0000�q\u0002K\u0001K\u0002K\u0000K\u0000�q\u0003K\u0002K\u0003K\u0000K\u0000�q\u0004K\u0003K\u0004K\u0000K\u0000�q\u0005K\u0004K\u0005K\u0000K\u0000�q\u0006K\u0005K\u0006K\u0000K\u0000�q\u0007K\u0006K\u0007K\u0000K\u0000�q\bK\u0007K\bK\u0000K\u0000�q\tK\bK\tK\u0000K\u0000�q",
        "K\tK",
        "K\u0000K\u0000�q"
      ]
    },
    "train.pickle": {
      "type": "ndarray",
      "repr": "[[103321     64  67873   1934   1985]\n [124322    185 118476   2013   2013]\n [ 46463    123  31858   1980   1983]\n ...\n [ 15681    123  28141   1986   1989]\n [ 82205     30  65824   1983   1983]\n [ 93"
    },
    "valid.pickle": {
      "type": "ndarray",
      "repr": "[[103321     64  67873   1934   1985]\n [124322    185 118476   2013   2013]\n [ 46463    123  31858   1980   1983]\n ...\n [ 48854     92  54284   1913   1922]\n [ 40731     18  31113   1979   1979]\n [  4"
    },
    "test.pickle": {
      "type": "ndarray",
      "repr": "[[113879     85 121588   1918   1919]\n [  4080    123  48949   1989   1994]\n [113408    123  31858   1957   1958]\n ...\n [  6165     59    715   1991   1991]\n [ 61028    123 103271   1921   1922]\n [ 14"
    },
    "event_list_all.pickle": {
      "type": "list",
      "len": 665948,
      "sample_keys": "(1, -1, (36934, 185, 6709))"
    },
    "ts_diffs.pickle": {
      "type": "ndarray",
      "repr": "[1. 1. 1. ... 1. 1. 1.]"
    }
  }
}
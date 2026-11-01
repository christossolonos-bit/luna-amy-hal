"""Build versioned gold cases from retained excerpts and independently annotated probes.

Use --wordnet-db to read a WordNet store. Never opens Adam's fact memory.
"""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from nlp.wordnet import WordNet

ROOT = Path(__file__).parent
WIKI_GOLD = {
    "Burbank, California": ("Where is Burbank?", ["Los Angeles County"], "Who was Burbank named after?", ["David Burbank"]),
    "Dayton, Ohio": ("Where is Dayton?", ["Ohio"], "What county is Dayton in?", ["Montgomery"]),
    "Ohio": ("What is Ohio?", ["state"], "What is the capital of Ohio?", ["Columbus"]),
    "Alabama": ("What is Alabama?", ["state"], "What borders Alabama to the north?", ["Tennessee"]),
    "Moon": ("What does the Moon orbit?", ["Earth"], "What is the Moon?", ["satellite"]),
    "Mars": ("What is Mars?", ["planet"], "What is Mars also called?", ["Red Planet"]),
    "Jupiter": ("What is Jupiter?", ["gas giant"], "What does Jupiter orbit?", ["Sun"]),
    "Saturn": ("What is Saturn?", ["gas giant"], "What does Saturn orbit?", ["Sun"]),
    "Earth": ("What is Earth?", ["planet"], "What covers most of Earth's crust?", ["ocean"]),
    "Sun": ("What is the Sun?", ["star"], "Where is the Sun?", ["Solar System"]),
    "Amazon River": ("Where is the Amazon River?", ["South America"], "What is the Amazon?", ["river"]),
    "Nile": ("Where is the Nile?", ["Africa"], "Where does the Nile empty?", ["Mediterranean Sea"]),
    "Mount Everest": ("What is Mount Everest?", ["mountain"], "Where is Mount Everest?", ["Himalayas"]),
    "Pacific Ocean": ("What bounds the Pacific Ocean to the west?", ["Asia", "Australia"], "What bounds the Pacific Ocean to the east?", ["Americas"]),
    "Sahara": ("What is the Sahara?", ["desert"], "Where is the Sahara?", ["Africa"]),
    "Photosynthesis": ("What does photosynthesis convert?", ["light energy"], "What does oxygenic photosynthesis release?", ["oxygen"]),
    "Telescope": ("What is a telescope?", ["device"], "What is a telescope used to observe?", ["distant objects"]),
    "Bicycle": ("What is a bicycle?", ["vehicle"], "What is a bicycle rider called?", ["cyclist"]),
    "Volcano": ("What escapes from a volcano?", ["lava"], "Where are most volcanoes found?", ["underwater"]),
    "Penguin": ("What are penguins?", ["birds"], "What do penguins use for swimming?", ["flippers"]),
    "Mercury (planet)": ("What is Mercury?", ["planet"], "What is Mercury's largest crater?", ["Caloris Planitia"]),
    "Venus": ("What is Venus?", ["planet"], "What is the atmosphere of Venus mostly composed of?", ["carbon dioxide"]),
    "Neptune": ("What does Neptune orbit?", ["Sun"], "Who was Neptune named after?", ["Roman god"]),
    "Uranus": ("What is Uranus?", ["ice giant"], "What is most of Uranus made of?", ["water", "ammonia", "methane"]),
    "Giraffe": ("What are giraffes?", ["mammals"], "What family are giraffes classified under?", ["Giraffidae"]),
    "Elephant": ("What are elephants?", ["animals"], "What is an elephant's long proboscis called?", ["trunk"]),
    "Oak": ("What genus is an oak in?", ["Quercus"], "What is an oak's nut called?", ["acorn"]),
    "Oxygen": ("What is oxygen?", ["chemical element"], "What is the atomic number of oxygen?", ["8"]),
    "Water": ("What is water?", ["compound"], "What is the chemical formula of water?", ["H2O"]),
    "Carbon dioxide": ("What is carbon dioxide?", ["compound"], "What is the chemical formula of carbon dioxide?", ["CO2"]),
    "Marie Curie": ("What was Marie Curie?", ["physicist"], "What elements did Marie Curie discover?", ["radium", "polonium"]),
    "Albert Einstein": ("What was Albert Einstein?", ["physicist"], "What theory is Einstein known for developing?", ["relativity"]),
}
NEWS_GOLD = {
    "news-01": ("Who writes the daily astronomy explanation?", ["astronomer"]),
    "news-02": ("Where is MAX POWER hosted?", ["Kennedy Space Center", "Florida"]),
    "news-03": ("What telescope captured the Cat's Head Nebula?", ["James Webb"]),
    "news-04": ("Who said Hubble still has important science ahead of it?", ["Jared Isaacman"]),
    "news-05": ("When did NASA launch Voyager 1 and 2?", ["1977"]),
    "news-07": ("Where is Vega Island?", ["Weddell Sea"]),
    "news-08": ("What meteorite does the map of Mars concern?", ["Teghaza 001"]),
    "news-09": ("What was the slice of Teghaza 001 used to determine?", ["age"]),
    "news-10": ("What do crew members aboard the International Space Station dress up in?", ["costumes"]),
}
WORDS = {"dog": ["Canis"], "cat": ["mammal"], "bird": ["feathers"], "fish": ["gills"],
         "tree": ["plant"], "flower": ["plant"], "river": ["stream"], "mountain": ["land mass"],
         "ocean": ["water"], "truck": ["vehicle"], "bicycle": ["two wheels"], "telescope": ["magnifier"],
         "microscope": ["magnifier"], "planet": ["celestial bodies"], "star": ["celestial body"],
         "volcano": ["fissure"], "island": ["water"], "library": ["books"], "hospital": ["treatment"], "school": ["institution"]}
CONVERSATIONS = [
    ("family", "My sister is named Jen, and she lives in Denver. Although she used to work at a school, she now works at a hospital. Her dog is named Milo, and he likes swimming.", [("Where does Jen live?", ["Denver"]), ("Where does Jen work now?", ["hospital"]), ("What is Jen's dog named?", ["Milo"])]),
    ("truck", "Howard, who lives in Columbus, owns a red truck that he drives to work. He works at a bakery, but his brother Sam works at a bank in Dayton.", [("What does Howard own?", ["truck"]), ("Where does Sam work?", ["bank"]), ("Where does Howard live?", ["Columbus"])]),
    ("negation", "Maria owns a bicycle, but she does not own a car. Luis owns a car and lives in Austin. Maria lives in Boston, where she works at a library.", [("Does Maria own a car?", ["no"]), ("What does Maria own?", ["bicycle"]), ("Where does Maria work?", ["library"])]),
    ("cause", "Because Owen was hungry, he ate pizza. His friend Grace, who is a nurse, brought him water after she finished work. Grace lives in Seattle.", [("What did Owen eat?", ["pizza"]), ("What is Grace?", ["nurse"]), ("Where does Grace live?", ["Seattle"])]),
    ("condition", "If someone owns a dog, they are a pet owner. Nina owns a dog named Bella, and she lives in Phoenix. If Nina buys a boat, she will sail to the island.", [("Is Nina a pet owner?", ["yes"]), ("Where does Nina live?", ["Phoenix"]), ("Does Nina own a boat?", [])]),
    ("hedging", "I think Marcus lives in Portland, but I am not sure. Elena definitely lives in Chicago, where she works at a bakery. Marcus might own a boat, although I have not seen it.", [("Where does Elena live?", ["Chicago"]), ("Where does Elena work?", ["bakery"])]),
    ("sequence", "After Priya visited the museum, she walked to a cafe. The cafe, which is near the library, serves tea and coffee. Priya bought tea, while her friend Dana bought coffee.", [("What did Priya buy?", ["tea"]), ("What did Dana buy?", ["coffee"]), ("What does the cafe serve?", ["tea", "coffee"])]),
    ("self", "You are named Adam, and I created you to learn about people. Although you can remember what I tell you, you do not have a body. I live in Ohio and I like music.", [("What is your name?", ["Adam"]), ("Where do I live?", ["Ohio"]), ("What do I like?", ["music"])]),
]


def probe(question, words, status="answer"):
    return {"question": question, "all_of": words, "status": status}


def build(wordnet):
    cases = []
    for row in json.loads((ROOT / "sources.json").read_text(encoding="utf-8")):
        if row["category"] == "wikipedia":
            q1, w1, q2, w2 = WIKI_GOLD[row["title"]]
            probes = [probe(q1, w1), probe(q2, w2)]
        else:
            q, words = NEWS_GOLD[row["id"]]
            probes = [probe(q, words)]
        cases.append({"id": row["id"], "category": row["category"], "text": row["text"],
                      "source": row["source"], "probes": probes,
                      "metadata": {k: v for k, v in row.items() if k not in ("id", "text", "source", "category")}})
    for word, expected in WORDS.items():
        sense = wordnet.usual(word)
        if sense is None:
            raise ValueError(f"Missing source definition: {word}")
        # Supply the headword so digest can resolve the otherwise implicit subject.
        # No clause splitting or output of gloss_sentences is used as benchmark input.
        definition = sense.definition
        if word == "bird":
            framed = "Birds are " + definition
        elif definition.startswith(("a ", "an ", "any ", "(")):
            framed = f"A {word} is {definition}"
        else:
            framed = f"A {word} is a {definition}"
        cases.append({"id": "wordnet-" + word, "category": "wordnet", "text": framed.rstrip(".") + ".",
                      "source": "wordnet:" + sense.id,
                      "probes": [probe(f"What is a {word}?", expected)],
                      "metadata": {"sense_id": sense.id, "original_definition": definition,
                                   "adaptation": "headword and copula supplied; definition retained verbatim",
                                   "url": "https://en-word.net/", "license": "OEWN CC BY 4.0; derived from Princeton WordNet"}})
    for name, text, questions in CONVERSATIONS:
        probes = [probe(q, words, "unknown" if not words else "no" if words == ["no"] else
                        "yes" if words == ["yes"] else "answer") for q, words in questions]
        cases.append({"id": "conversation-" + name, "category": "conversation", "text": text,
                      "source": "said:Evaluator", "probes": probes,
                      "metadata": {"origin": "authored fictional evaluation dialogue; not a real user's private conversation",
                                   "license": "project-authored fixture"}})
    return {"schema": 1, "cases": cases}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wordnet-db", required=True, type=Path)
    args = parser.parse_args()
    data = build(WordNet(args.wordnet_db))
    (ROOT / "cases.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(data['cases'])} paragraph cases")

import os
import loading
from collections import defaultdict
import gzip
import csv
import evaluation
import operator

def makeExamples(proteins, limitTerms, limitToSets=None, predKey="predictions"):
    print "Converting proteins to examples"
    examples = {"predictions":[], "labels":[], "ids":[], "cafa_ids":[], "sets":[], "label_names":[], "label_size":{}}
    protIds = sorted(proteins.keys())
    protObjs = [proteins[key] for key in protIds]
    filtered = {"labels":set(), "predictions":set()}
    for protein in protObjs:
        if limitToSets != None and not any(x in limitToSets for x in protein["sets"]):
            continue
        # Build labels
        labels = protein["terms"].keys()
        labels = sorted(labels)
        if limitTerms:
            filtered["labels"].update([x for x in labels if x not in limitTerms])
            labels = [x for x in labels if x in limitTerms]
        for label in labels:
            if label not in examples["label_size"]:
                examples["label_size"][label] = 0
            examples["label_size"][label] += 1
        examples["labels"].append(labels)
        examples["ids"].append(protein["id"])
        examples["cafa_ids"].append(protein["cafa_ids"])
        examples["sets"].append(protein["sets"])
        if predKey != None:
            predictions = sorted(protein.get(predKey, {}).keys())
            if limitTerms:
                filtered["labels"].update([x for x in predictions if x not in limitTerms])
                predictions = [x for x in predictions if x in limitTerms]
            examples["predictions"].append(predictions)
    #for protein in protObjs:
    #    examples["predictions"].append(sorted(protein.get(predKey, {}).keys()))
    #    #for pred in examples["predictions"][-1]:
    #    #    assert pred in examples["label_size"], pred
    print "Converted", len(proteins), "proteins into", len(examples["labels"]), "examples, filtered terms:", {x:len(filtered[x]) for x in filtered}
    return examples

def limitExamples(examples, limitToSets):
    indices = [i for i in range(len(examples["sets"])) if any(x in limitToSets for x in examples["sets"][i])]
    print "Limiting", examples["labels"].shape[0], "to", len(indices)
    for key in ("ids", "cafa_ids", "sets"):
        examples[key] = [examples[key][i] for i in indices]
    for key in ("labels", "predictions"):
        examples[key] = examples[key][indices]

def loadPredictions(proteins, inPath, limitToSets, readGold=True, predKey="predictions", confKey=None, includeDuplicates=False):
    print "Loading predictions from", inPath
    with gzip.open(inPath, "rt") as f:
        reader = csv.DictReader(f, delimiter='\t')
        protein = None
        rowCountKey = "rows:" + predKey
        counts = {"duplicates":0, "duplicates-preserved":0, rowCountKey:0, "protein-not-loaded":0, "out-of-sets":0, "predicted_1":0}
        currentId = None
        for row in reader:
            if currentId != row["id"]:
                currentId = row["id"]
                if row["id"] in proteins:
                    protein = proteins[row["id"]]
                    if limitToSets != None and not any(x in limitToSets for x in protein["sets"]):
                        counts["out-of-sets"] += 1
                        protein = None
                    elif predKey in protein and not includeDuplicates:
                        counts["duplicates"] += 1
                        protein = None
                    else:
                        if predKey in protein:
                            counts["duplicates"] += 1
                            counts["duplicates-preserved"] += 1
                        else:
                            protein[predKey] = {}
                        counts[rowCountKey] += 1
                        if confKey != None and confKey not in protein:
                            protein[confKey] = {}
                        if readGold:
                            if not includeDuplicates:
                                assert "gold" not in protein
                            protein["gold"] = {}
                else:
                    counts["protein-not-loaded"] += 1
                    protein = None
            if protein != None:
                if row["predicted"] == "1":
                    counts["predicted_1"] += 1
                    protein[predKey][row["label"]] = 1
                    if confKey != None:
                        protein[confKey][row["label"]] = float(row["confidence"])
                if readGold and row["gold"] == "1":
                    protein["gold"][row["gold"]] = 1
    print "Predictions loaded:", counts

def getTopTerms(counts, num=1000):
    return sorted(counts.items(), key=operator.itemgetter(1), reverse=True)[0:num]

def evaluateFile(inPath, dataPath, setNames, numTerms=5000, cafaTargets="skip", useHPO=False, detailed=False):
    assert cafaTargets in ("skip", "overlap", "separate", "external")
    print "==========", "Evaluating", "=========="
    terms = None
    if detailed:
        if useHPO:
            terms = loading.loadOBOTerms(os.path.join(options.dataPath, "HPO", "ontology", "hp.obo"), onlyNames=True, forceNameSpace="obo")
        else:
            terms = loading.loadGOTerms(os.path.join(options.dataPath, "GO", "go_terms.tsv"))
    proteins = defaultdict(lambda: dict())
    print "Loading Swissprot proteins"
    loading.loadFASTA(os.path.join(options.dataPath, "Swiss_Prot", "Swissprot_sequence.tsv.gz"), proteins)
    if cafaTargets != "skip":
        print "Loading CAFA3 targets"
        loading.loadFASTA(os.path.join(options.dataPath, "CAFA3_targets", "Target_files", "target.all.fasta"), proteins, True)
    print "Proteins:", len(proteins)
    if useHPO:
        loading.removeNonHuman(proteins)
        termCounts = loading.loadHPOAnnotations(os.path.join(options.dataPath, "HPO", "annotation", "all_cafa_annotation_propagated.tsv.gz"), proteins)
    else:
        termCounts = loading.loadAnnotations(os.path.join(options.dataPath, "data", "Swissprot_propagated.tsv.gz"), proteins)
    print "Unique terms:", len(termCounts)
    topTerms = getTopTerms(termCounts, numTerms)
    print "Using", len(topTerms), "most common GO terms"
    loading.loadSplit(os.path.join(options.dataPath, "data"), proteins, allowMissing=useHPO)
    loading.defineSets(proteins, cafaTargets)
    
    loadPredictions(proteins, inPath, setNames)
    examples = makeExamples(proteins, limitTerms=set([x[0] for x in topTerms]), predKey="predictions")
    #print "labels", examples["labels"][0:500]
    #print "predictions", examples["predictions"][0:500]
    loading.vectorizeExamples(examples, None, sparseLabels=not detailed)
    limitExamples(examples, setNames)
    results = evaluation.evaluate(examples["labels"], examples["predictions"], examples, terms=terms, averageOnly=not detailed, noAUC=True)
    print setNames, "average:", evaluation.metricsToString(results["average"])
    if detailed:
        print "------", "Detailed results", "------"
        print evaluation.getResultsString(results, 20, ["average"])
        print "------", "Detailed results table", "------"
        print evaluation.getResultsTable(results, 20, ["average"])

if __name__=="__main__":       
    from optparse import OptionParser
    optparser = OptionParser(description="")
    optparser.add_option("-i", "--input", default=None, help="")
    optparser.add_option("-p", "--dataPath", default=os.path.expanduser("~/data/CAFA3/data"), help="")
    optparser.add_option("-s", "--setNames", default=None, help="")
    optparser.add_option("-t", "--terms", default=5000, type=int, help="The number of top most common GO terms to use as labels")
    optparser.add_option("--targets", default="skip", help="How to include the CAFA target proteins, one of 'skip', 'overlap' or 'separate'")
    optparser.add_option("--hpo", default=False, action="store_true")
    optparser.add_option("--detailed", default=False, action="store_true")
    (options, args) = optparser.parse_args()
    
    options.setNames = [x.strip() for x in options.setNames.split(",")]
    evaluateFile(options.input, options.dataPath, options.setNames, options.terms, cafaTargets=options.targets, useHPO=options.hpo, detailed=options.detailed)
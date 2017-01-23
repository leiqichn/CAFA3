import sys, os
import gzip
from collections import defaultdict
import csv
from featureBuilders import *
from utils import Stream
import operator
import time
from sklearn.cross_validation import train_test_split
from sklearn.metrics.ranking import roc_auc_score
import shutil
try:
    import ujson as json
except ImportError:
    import json
import statistics
from classification import Classification, SingleLabelClassification
import loading

# from sklearn.utils.validation import check_X_y, has_fit_parameter
# from sklearn.externals.joblib.parallel import Parallel, delayed
# class MyMultiOutputClassifier(MultiOutputClassifier):
#     def fit(self, X, y, sample_weight=None):
#         if not hasattr(self.estimator, "fit"):
#             raise ValueError("The base estimator should implement a fit method")
#     
#         X, y = check_X_y(X, y,
#                          multi_output=True,
#                          accept_sparse=True)
#     
#         if y.ndim == 1:
#             raise ValueError("y must have at least two dimensions for "
#                              "multi target regression but has only one.")
#     
#         if (sample_weight is not None and
#                 not has_fit_parameter(self.estimator, 'sample_weight')):
#             raise ValueError("Underlying regressor does not support"
#                              " sample weights.")
#     
#         self.estimators_ = Parallel(n_jobs=self.n_jobs, verbose=3)(delayed(_fit_estimator)(
#             self.estimator, X, y[:, i], sample_weight) for i in range(y.shape[1]))
#         return self

#MultiOutputClassifier.fit = newFit
        
# def splitProteins(proteins):
#     datasets = {"devel":[], "train":[], "test":[]}
#     for protId in sorted(proteins.keys()):
#         datasets[proteins[protId]["set"]].append(proteins[protId])
#     print "Divided sets", [(x, len(datasets[x])) for x in sorted(datasets.keys())]
#     return datasets

def getFeatureGroups(groups=None):
    if groups == None:
        groups = ["all"]
    groups = list(set(groups))
    if "all" in groups:
        groups.remove("all")
        groups += set(["taxonomy", "blast", "delta", "interpro", "predgpi", "nucpred"])
    removed = [x for x in groups if x.startswith("-")]
    groups = [x for x in groups if not x.startswith("-")]
    for group in removed:
        groups.remove(group.strip("-"))
    return groups

def buildExamples(proteins, dataPath, limit=None, limitTerms=None, featureGroups=None):
    print "Building examples"
    examples = {"labels":[], "features":[], "ids":[], "cafa_ids":[], "sets":[], "label_names":[], "label_size":{}}
    protIds = sorted(proteins.keys())
    if limit:
        protIds = protIds[0:limit]
    protObjs = [proteins[key] for key in protIds]
    for protein in protObjs:
        # Initialize features
        protein["features"] = {"DUMMY:dummy":1}
        # Build labels
        labels = protein["terms"].keys()
        if limitTerms:
            labels = [x for x in labels if x in limitTerms]
        labels = sorted(labels)
        #if len(labels) == 0:
        #    labels = ["no_annotations"]
        for label in labels:
            if label not in examples["label_size"]:
                examples["label_size"][label] = 0
            examples["label_size"][label] += 1
        examples["labels"].append(labels)
        examples["ids"].append(protein["id"])
        examples["cafa_ids"].append(protein["cafa_ids"])
        examples["sets"].append(protein["sets"])
    # Build features
    featureGroups = getFeatureGroups(featureGroups)
    for group in featureGroups:
        if group not in ("taxonomy", "similar", "blast", "blast62", "delta", "interpro", "predgpi", "nucpred"):
            raise Exception("Unknown feature group '" + str(group) + "'")
    print "Building features, feature groups =", featureGroups
    if featureGroups == None or "taxonomy" in featureGroups:
        builder = TaxonomyFeatureBuilder([os.path.join(dataPath, "Taxonomy")])
        builder.build(protObjs)
    if featureGroups == None or "similar" in featureGroups:
        builder = UniprotFeatureBuilder(os.path.join(dataPath, "Uniprot", "similar.txt"))
        builder.build(protObjs)
    if featureGroups == None or "blast" in featureGroups:
        builder = BlastFeatureBuilder([os.path.join(dataPath, "temp_blastp_result_features"), os.path.join(dataPath, "blastp_result_features")])
        builder.build(protObjs)
    if featureGroups == None or "blast62" in featureGroups:
        builder = BlastFeatureBuilder([os.path.join(dataPath, "CAFA2", "training_features"), os.path.join(dataPath, "CAFA2", "CAFA3_features")])
        builder.build(protObjs)
    if featureGroups == None or "delta" in featureGroups:
        builder = BlastFeatureBuilder([os.path.join(dataPath, "temp_deltablast_result_features"), os.path.join(dataPath, "deltablast_result_features")], tag="DELTA")
        builder.build(protObjs)
    if featureGroups == None or "interpro" in featureGroups:
        builder = InterProScanFeatureBuilder([os.path.join(dataPath, "temp_interproscan_result_features"), os.path.join(dataPath, "interproscan_result_features")])
        builder.build(protObjs)
    if featureGroups == None or "predgpi" in featureGroups:
        builder = GPIAnchoringFeatureBuilder([os.path.join(dataPath, "predGPI")])
        builder.build(protObjs)
    if featureGroups == None or "nucpred" in featureGroups:
        builder = NucPredFeatureBuilder([os.path.join(dataPath, "nucPred")])
        builder.build(protObjs)
    builder = None
    examples["features"] = [x["features"] for x in protObjs]
    for protObj in protObjs:
        del protObj["features"]
    # Prepare the examples
    print "Built", len(examples["labels"]), "examples" # with", len(examples["feature_names"]), "unique features"
    return examples
    #return mlb.fit_transform(examples["labels"]), dv.fit_transform(examples["features"])

def getTopTerms(counts, num=1000):
    return sorted(counts.items(), key=operator.itemgetter(1), reverse=True)[0:num]

def run(dataPath, outDir=None, actions=None, featureGroups=None, classifier=None, classifierArgs=None, limit=None, numTerms=100, useTestSet=False, clear=False, cafaTargets="skip", negatives=False, singleLabelJobs=None):
    if clear and os.path.exists(outDir):
        print "Removing output directory", outDir
        shutil.rmtree(outDir)
    if not os.path.exists(outDir):
        print "Making output directory", outDir
        os.makedirs(outDir)
    Stream.openLog(os.path.join(options.output, "log.txt"))
    if actions != None:
        for action in actions:
            assert action in ("build", "classify", "statistics")
    assert cafaTargets in ("skip", "overlap", "separate", "external")
    #loadUniprotSimilarity(os.path.join(options.dataPath, "Uniprot", "similar.txt"), proteins)
    terms = loading.loadGOTerms(os.path.join(options.dataPath, "GO", "go_terms.tsv"))
    
    exampleFilePath = os.path.join(outDir, "examples.json.gz")
    examples = None
    if actions == None or "build" in actions:
        print "==========", "Building Examples", "=========="
        proteins = defaultdict(lambda: dict())
        loading.loadFASTA(os.path.join(options.dataPath, "Swiss_Prot", "Swissprot_sequence.tsv.gz"), proteins)
        if cafaTargets != "skip":
            print "Loading CAFA3 targets"
            loading.loadFASTA(os.path.join(options.dataPath, "CAFA3_targets", "Target_files", "target.all.fasta"), proteins, True)
        print "Proteins:", len(proteins)
        termCounts = loading.loadAnnotations(os.path.join(options.dataPath, "data", "Swissprot_propagated.tsv.gz"), proteins)
        print "Unique terms:", len(termCounts)
        topTerms = getTopTerms(termCounts, numTerms)
        print "Using", len(topTerms), "most common GO terms"
        #print "Most common terms:", topTerms
        #print proteins["14310_ARATH"]
        loading.loadSplit(os.path.join(options.dataPath, "data"), proteins)
        loading.defineSets(proteins, cafaTargets)
        #divided = splitProteins(proteins)
        examples = buildExamples(proteins, dataPath, limit, limitTerms=set([x[0] for x in topTerms]), featureGroups=featureGroups)
        print "Saving examples to", exampleFilePath
        with gzip.open(exampleFilePath, "wt") as pickleFile:
            json.dump(examples, pickleFile, indent=2) #pickle.dump(examples, pickleFile)
        loading.vectorizeExamples(examples, featureGroups)
    elif len([x for x in actions if x != "build"]) > 0:
        print "==========", "Loading Examples", "=========="
        if examples == None:
            print "Loading examples from", exampleFilePath
            with gzip.open(exampleFilePath, "rt") as pickleFile:
                examples = json.load(pickleFile) #pickle.load(pickleFile)
        loading.vectorizeExamples(examples, featureGroups)
    if actions == None or "classify" in actions:
        print "==========", "Training Classifier", "=========="
        if not os.path.exists(os.path.join(outDir, "features.tsv")):
            loading.saveFeatureNames(examples["feature_names"], os.path.join(outDir, "features.tsv"))
        if singleLabelJobs == None:
            cls = Classification()
        else:
            cls = SingleLabelClassification(singleLabelJobs)
        cls.optimize(classifier, classifierArgs, examples, terms=terms, 
                     outDir=outDir, negatives=negatives,
                     useTestSet=useTestSet, useCAFASet=(cafaTargets != "skip"))
    if actions == None or "statistics" in actions:
        print "==========", "Calculating Statistics", "=========="
        statistics.makeStatistics(examples, outDir)
    #y, X = buildExamples(proteins, None, set([x[0] for x in topTerms]))
    #print y
    #print X
    #print time.strftime('%X %x %Z')
    #classify(y, X)
    #print time.strftime('%X %x %Z')

if __name__=="__main__":       
    from optparse import OptionParser
    optparser = OptionParser(description="")
    optparser.add_option("-a", "--actions", default=None, help="One or more of 'build', 'classify' or 'statistics'")
    optparser.add_option("-p", "--dataPath", default=os.path.expanduser("~/data/CAFA3/data"), help="The main directory for the data files")
    optparser.add_option("-f", "--features", default="all", help="Comma-separated list of feature group names. Use 'all' for all feature groups and '-name' to remove groups.")
    optparser.add_option("-l", "--limit", default=None, type=int, help="Limit the number of proteins to read.")
    optparser.add_option("-t", "--terms", default=100, type=int, help="The number of top most common GO terms to use as labels")
    optparser.add_option("-o", "--output", default=None, help="The output directory")
    optparser.add_option('-c','--classifier', help='', default="ensemble.RandomForestClassifier")
    optparser.add_option('-r','--args', help='', default="{'random_state':[1], 'n_estimators':[10], 'n_jobs':[1], 'verbose':[3]}")
    #optparser.add_option("--multioutputclassifier", default=False, action="store_true", help="Use the MultiOutputClassifier to train a separate classifier for each label")
    optparser.add_option("--singleLabelJobs", default=None, type=int, help="Number of jobs for SingleLabelClassification")
    optparser.add_option("--testSet", default=False, action="store_true", help="Classify the test set")
    optparser.add_option("--clear", default=False, action="store_true", help="Remove the output directory if it already exists")
    optparser.add_option("--targets", default="skip", help="How to include the CAFA target proteins, one of 'skip', 'overlap' or 'separate'")
    optparser.add_option("--negatives", default=False, action="store_true", help="Write negative predictions in the result files")
    (options, args) = optparser.parse_args()
    
    if options.actions != None:
        options.actions = options.actions.split(",")
    options.args = eval(options.args)
    #proteins = de
    #importProteins(os.path.join(options.dataPath, "Swiss_Prot", "Swissprot_sequence.tsv.gz"))
    run(options.dataPath, actions=options.actions, featureGroups=options.features.split(","), 
        limit=options.limit, numTerms=options.terms, useTestSet=options.testSet, outDir=options.output,
        clear=options.clear, classifier=options.classifier, classifierArgs=options.args, 
        cafaTargets=options.targets, negatives=options.negatives, singleLabelJobs=options.singleLabelJobs)
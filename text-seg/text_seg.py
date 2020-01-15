"""
Goal: Segment text into sections
Task: Classify a pair of sentences into "from the same section or not"

Data dir (shanglinghsu@): ~/ml-camp/wiki-vandalism/json


*** Before using this code ***
* default pytorch conda ve provided: conda activate pytorch
* pip install --user transformers nltk
* In python, run this
  >>> import nltk
  >>> nltk.download('punkt')

"""
import argparse
import json
import os
import random
import re
import string
from itertools import combinations

from nltk.tokenize import sent_tokenize
from tqdm import tqdm, trange

import torch
from torch.utils.data import (DataLoader, RandomSampler, TensorDataset,
                              random_split)
from transformers import (AdamW, BertForSequenceClassification, BertTokenizer,
                          get_linear_schedule_with_warmup)
from utils import remove_non_printable, traverse_json_dir

# CUDA for PyTorch
use_cuda = torch.cuda.is_available()
device = torch.device("cuda:0" if use_cuda else "cpu")

# mini-json: Subset with only 7822*.json and 7823*.json
json_dir = "/home/shanglinghsu/ml-camp/wiki-vandalism/mini-json" # Should be json
#model_dir = "/home/shanglinghsu/ml-camp/wiki-vandalism/mini-json-raw/pregen/models/"

#for d in [model_dir]: #loss_dir
#    if not os.path.exists(d):
#        os.makedirs(d)

seed = 666
def set_seed(seed):
    random.seed(seed)
    #np.random.seed(seed)
    torch.manual_seed(seed)
#set_seed(seed)

def get_inputs_labels(json_dir):
    # Data (subset) -> Dataset
    docs = traverse_json_dir(json_dir, return_docs=True)
    inputs, labels = [],[]
    # docs之間無關
    for sections in docs:
        # secs之間是1
        for i in range(len(sections)-1):
            inputs.append((sections[i][-1], sections[i+1][0]))
            labels.append(1)

        # sec內sents間是0
        for sents in sections:
            for i in range(len(sents)-1):
                inputs.append((sents[i], sents[i+1]))
                labels.append(0)
    return inputs, labels

# pregen.py
def create_instances_from_json(max_seq_length):

    tokenizer_encode_plus_parameters = { 'max_length' : max_seq_length, 'pad_to_max_length' : 'right'}#, 'return_tensors' : 'pt',}

    inputs, labels = get_inputs_labels(json_dir = "/home/shanglinghsu/ml-camp/wiki-vandalism/mini-json") # Should be json #json_dir)
    print("*** Batch encoding ***")
    enc =  tokenizer.batch_encode_plus(inputs, **tokenizer_encode_plus_parameters)
    print("--- Batch encoding done ---")
    
    instances = []
    for input_id, token_type_id, label in zip(enc['input_ids'], enc['token_type_ids'], labels):
        instances.append({
            "tokens": tokenizer.convert_ids_to_tokens(input_id),
            "segment_ids": token_type_id,
            "is_random_next": label,
            "masked_lm_positions": [],
            "masked_lm_labels": []
        })
    random.shuffle(instances)
    return instances

def create_training_json(args):
    epoch_filename = args.output_dir / "epoch_0.json"
    num_instances = 0
    with epoch_filename.open('w') as epoch_file:
        doc_instances = create_instances_from_json(
            max_seq_length=args.max_seq_len)
        doc_instances = [json.dumps(instance) for instance in doc_instances]
        for instance in doc_instances:
            epoch_file.write(instance + '\n')
            num_instances += 1
    metrics_file = args.output_dir / "epoch_0_metrics.json"
    with metrics_file.open('w') as metrics_file:
        metrics = {
            "num_training_examples": num_instances,
            "max_seq_len": args.max_seq_len
        }
        metrics_file.write(json.dumps(metrics))

class TextSplitter():
    """
    ########################################
    #                Usage                 #
    ########################################
    # Please make sure that the dependencies 
    # in the begginning of this doc are 
    # properly installed before using this

    model_dir = "/home/shanglinghsu/ml-camp/wiki-vandalism/mini-json-raw/pregen/models/"
    text = r"This is the text to be split. Many sentences. Sometimes '\n'."
    
    # It takes time to initialize this, so 
    # reusing the same during one session 
    # should be a better idea.
    splitter = TextSplitter(model_dir) 
    segments = splitter.split(text)
    """
    def __init__(self, model_dir):
        self.tokenizer = BertTokenizer.from_pretrained(model_dir)
        self.model = BertForSequenceClassification.from_pretrained(model_dir, output_attentions=True)#'bert-base-cased')

    def split(self, text):
        """
        The main API of this code.
        Splits text according to:
        1) '\n'
        2) Semantic

        : text: str -- Normal text input
        : segments: list(str) -- Each str is a semantic segment.
        """
        segments, key_phrases = [], []
        segment_counter = 0
        paragraphs = [t for t in text.split('\n') if len(t) > 0]
        for paragraph in paragraphs:
            segments.append([]) # 1) paragraph (split by '\n')
            key_phrases.append([])
            sents = sent_tokenize(paragraph)
            for i in range(len(sents)-1):
                # "Current" and "next" sentences
                input_ids = self.tokenizer.encode_plus(sents[i],sents[i+1], return_tensors='pt')['input_ids']
                logits, attentions = self.model(input_ids)
                input_ids = input_ids.squeeze(0)
                logits = logits.squeeze(0)
                
                ## Keyphrase is the most attented input
                key_phrase = self.tokenizer.decode([input_ids[attentions[-1][0,-1,:,0].argmax()]])
                #current sentences = self.tokenizer.decode(input_ids)
                
                ## Update list with this result
                segments[-1].append(sents[i])
                key_phrases[-1].append(key_phrase)
                
                ## Split paragraph
                softmax = torch.nn.functional.softmax(logits)
                argmax = softmax.argmax().item()
                segments.append([]) # 2) semantic segment
                key_phrases.append([])
                segment_counter += argmax # 1 if diff; 0 otherwise
            
            # The last sentence
            input_ids = self.tokenizer.encode_plus(sents[-1], return_tensors='pt')['input_ids']
            _, attentions = self.model(input_ids)
            input_ids = input_ids.squeeze(0)
            key_phrase = self.tokenizer.decode([input_ids[attentions[-1][0,-1,:,0].argmax()]])
            
            ## Update list for the last one
            segments[-1].append(sents[-1])
            key_phrases[-1].append(key_phrase)

        return segments, key_phrases

"""
def test_model(model, device, tokenizer): # generator
    
    inputs, labels = get_inputs_labels(json_dir)
    data = TensorDataset(torch.cat(inputs), torch.tensor(labels))
    n_test = int(len(labels)*0.2)
    n_train = len(labels) - n_test
    train_set, valid_set = random_split(data, [n_train, n_test])
    train_generator = DataLoader(train_set, sampler=RandomSampler(train_set), batch_size=batch_size)
    valid_generator = DataLoader(valid_set, sampler=RandomSampler(valid_set), batch_size=batch_size)
    
    # Transfer to GPU
    vl_loss = []
    model = model.to(device)

    for local_batch, local_labels in valid_generator:
        local_batch, local_labels = local_batch.to(device), local_labels.to(device)

        # Model computations
        outputs = model(local_batch, labels=local_labels)
        loss, logits = outputs[:2]
        vl_loss.append(loss)
        print("loss:",loss)
        for b,l,ll in zip(local_batch, logits, local_labels):
            print(l.tolist(),'\t',ll.tolist(), '\t', tokenizer.decode(b))
        break

# Load model
model = BertForSequenceClassification.from_pretrained(model_dir)#'bert-base-cased')
tokenizer = BertTokenizer.from_pretrained(model_dir)#'bert-base-cased')

#test_model(model, device, tokenizer)
"""

import torch
from torch.utils.data import DataLoader
from abc import ABC, abstractmethod
from torchtext.data.utils import get_tokenizer
from torch.nn.functional import pad
from collections import Counter
from torchtext.vocab import Vocab
import warnings
import os
import json
import jsonpickle

Tensor = torch.Tensor

if torch.cuda.is_available():
    DEVICE = torch.device("cuda")
else:
    DEVICE = torch.device("cpu")


class AbstractPreprocessing(ABC):
    """The abstract class to define the interface of preprocessing
    """
    @abstractmethod
    def transform(self, *args, **kwargs):
        """This method deals with datum-wise transformations. This
        method is called in the Datasets to transform the output
        of ``__getitem__``"""
        pass

    @abstractmethod
    def fit_to_data(self, *args, **kwargs):
        """This method deals with getting dataset-level information.
        """
        pass

    def save_pretrained(self, path):
        with open(os.path.join(path, self.__class__.__name__ + ".json"), "w") as outfile:
            whole_class = jsonpickle.encode(self)
            json.dump(whole_class, outfile)

    def load_pretrained(self, path):
        try:
            with open(os.path.join(path,self.__class__.__name__ + ".json"), "r") as infile:
                whole_class = json.load(infile)
                self = jsonpickle.decode(whole_class)
        except FileNotFoundError:
            warnings.warn("The transformation file does not exist; attempting to run"
                          " the transformation anyway...")

class Normalisation(AbstractPreprocessing):
    """This class runs the standard normalisation on all the dimensions of
    the input tensor. For example, in case of images where each item is of
    shape ``(BS, C, H, W)``, the average will and the standard deviations
    will be tensors of shape ``(C, H, W)``
    """
    is_fitted: bool
    mean: Tensor

    def __init__(self):
        self.is_fitted = False

    def fit_to_data(self, data: Tensor):
        self.mean = self._mean(data, 0, False)
        self.stddev = self._stddev(data, 0, False)
        self.is_fitted = True
        self.save_pretrained(".")

    def transform(self, datum: Tensor) -> Tensor:
        if not self.is_fitted:
            self.load_pretrained(".")
        if not all(self.stddev>0):
            warnings.warn("The standard deviation contains zeros! Adding 1e-7")
            self.stddev = self.stddev + 1e-7
        out = (datum - self.mean)/(self.stddev)
        return out

    def _mean(self, data, dim, keep_dim):
        if isinstance(data, torch.utils.data.Dataset):
            data=next(iter(DataLoader(data, batch_size=len(data))))[0]
        return torch.mean(data.float(), dim, keep_dim)

    def _stddev(self, data, dim, keep_dim):
        if isinstance(data, torch.utils.data.Dataset):
            data=next(iter(DataLoader(data, batch_size=len(data))))[0]
        return torch.std(data.float(), dim, keep_dim)


class PreprocessingPipeline(AbstractPreprocessing):
    """class to compose preprocessing classes

    Args:
        list_of_cls (list):
            list of class instances
    """
    def __init__(self, list_of_preproc_and_datatypes):
        self.list_of_cls = list_of_preproc_and_datatypes
        
    def fit_to_data(self, data, **kwargs):
        for (preproc_cls, data_type_class) in self.list_of_cls:
            preproc_cls.fit_to_data(data, **kwargs)
            data = data_type_class(data, preproc_cls, **kwargs)

    def transform(self, batch):
        for (cls, dt) in self.list_of_cls:
            batch = cls.transform(batch)
        return batch

    def __len__(self) -> int:
        return len(self.list_of_cls)

    def __getitem__(self, index: int):
        return self.list_of_cls[index]

    def __iter__(self):
        return iter(self.list_of_cls)

    def __repr__(self) -> str:
        return f'Pipeline({self.list_of_cls})'

    def __add__(self, other):
        return PreprocessingPipeline(self.list_of_cls + other.list_of_cls)


class PreprocessTextData(AbstractPreprocessing):
    """Preprocessing class. This class is useful to convert the data format
    ``(label, text)`` into the proper tensor format ``( word_embedding, label)``

    Args:
        tokenizer (torch Tokenizer):
            the tokenizer of the source text
        vocabulary (torch Vocabulary):
            the vocubulary; it can be built of it can be
            given.
        kwargs (dict):
            keyword arguments for the ``Vocab``
    """
    def __init__(self, tokenizer=None,
                 vocabulary=None):
        if tokenizer is None:
            self.tokenizer = get_tokenizer('basic_english')
        else:
            self.tokenizer = tokenizer

        self.vocabulary = vocabulary
        self.MAX_LENGTH = 0
        self.is_fitted = False

    def fit_to_data(self, data):
        """Method to extract global data, like to length of
        the sentences to be able to pad.

        Args:
            data (torch dataset):
                the data in the format ``(label, text)``
        """

        counter = Counter()  # for the text
        for (label, text) in data:
            if isinstance(text, tuple) or isinstance(text, list):
                text = text[0]
            counter.update(self.tokenizer(text))
            self.MAX_LENGTH = max(self.MAX_LENGTH, len(self.tokenizer(text)))
        # self.vocabulary = Vocab(counter, min_freq=1)
        if self.vocabulary is None:
            self.vocabulary = Vocab(counter)
        self.is_fitted = True
        self.save_pretrained(".")

    def transform(self, datum: torch.Tensor) -> list:
        """This method is applied to each batch and
        transforms it following the rule below

        Args:
            datum (torch.tensor):
                a single datum
        """
        if not self.is_fitted:
            self.load_pretrained(".")
        text_pipeline = lambda x: [self.vocabulary[token] for token in
                                   self.tokenizer(x)]

        pad_item = self.vocabulary["."]

        _text = datum
        if isinstance(_text, tuple) or isinstance(_text, list):
            _text = _text[0]
        processed_text = torch.tensor(text_pipeline(_text),
                                      dtype=torch.int64).to(DEVICE)
        # convert to tensors (padded)
        out = torch.cat([processed_text,
                   pad_item * torch.ones(self.MAX_LENGTH - processed_text.shape[0]
                                              ).to(DEVICE)])
        return out


class PreprocessTextLabel(AbstractPreprocessing):
    def __init__(self, tokenizer=None, **kwargs):
        pass

    def fit_to_data(self, dataset):
        pass

    def transform(self, datum: torch.Tensor) -> torch.Tensor:
        label_pipeline = lambda x: torch.tensor(x, dtype=torch.long) - 1

        _label = datum
        try:
            label_pipeline(_label).to(DEVICE)
        except TypeError:
            if isinstance(_label, tuple) or isinstance(_label, list):
                _label = _label[0]
        out = label_pipeline(_label).to(DEVICE)

        return out


class PreprocessTextTranslation(AbstractPreprocessing):
    """Class to preprocess text dataloaders for translation
    tasks

        Args:
            vocabulary (torch Vocabulary):
                the vocubulary; it can be built of it can be
                given.
            tokenizer (torch Tokenizer):
                the tokenizer of the source text
            tokenizer_lab (torch Tokenizer):
                the tokenizer of the target text
        """

    def __init__(self, vocabulary=None,
                 vocabulary_target=None,
                 tokenizer=None,
                 tokenizer_target=None):

        if tokenizer is None:
            self.tokenizer = get_tokenizer('basic_english')
        else:
            self.tokenizer = tokenizer
        if tokenizer_target is None:
            self.tokenizer_target = get_tokenizer('basic_english')
        else:
            self.tokenizer_target = tokenizer_target
        self.vocabulary = vocabulary
        self.vocabulary_target = vocabulary_target
        self.MAX_LENGTH = 0
        self.is_fitted = False

    def fit_to_data(self, data):
        counter = Counter()  # for the text
        counter_target = Counter()  # for the text
        for text in data:
            counter.update(self.tokenizer(text[0]))
            counter_target.update(self.tokenizer_target(text[1]))
            self.MAX_LENGTH = max(self.MAX_LENGTH, len(self.tokenizer(text[0])))
            self.MAX_LENGTH = max(self.MAX_LENGTH, len(self.tokenizer_target(text[1])))
        # self.vocabulary = Vocab(counter, min_freq=1)
        if self.vocabulary is None:
            self.vocabulary = Vocab(counter)
        if self.vocabulary_target is None:
            self.vocabulary_target = Vocab(counter_target)
        self.is_fitted = True
        self.save_pretrained(".")

    def transform(self, datum):
        """This method is applied to each batch and
                transforms it following the rule below

                Args:
                    datum (torch.tensor):
                        a single datum
                """
        if not self.is_fitted:
            self.load_pretrained(".")
        text_pipeline = lambda x: [self.vocabulary[token] for token in
                                   self.tokenizer(x)]
        text_pipeline_target = lambda x: [self.vocabulary_target[token] for token in
                                   self.tokenizer_target(x)]

        pad_item = self.vocabulary["."]
        pad_item_target = self.vocabulary_target["."]

        processed_text = torch.tensor(text_pipeline(datum[0]),
                                      dtype=torch.int64).to(DEVICE)
        processed_text_target = torch.tensor(text_pipeline(datum[1]),
                                      dtype=torch.int64).to(DEVICE)
        # convert to tensors (padded)
        out = torch.cat([processed_text,
                         pad_item * torch.ones(self.MAX_LENGTH - processed_text.shape[0]
                                               ).to(DEVICE)])
        out_target = torch.cat([processed_text_target,
                         pad_item_target * torch.ones(self.MAX_LENGTH - processed_text_target.shape[0]
                                               ).to(DEVICE)])
        return out, out_target


class PreprocessTextQA(AbstractPreprocessing):
    """Class to preprocess text dataloaders for Q&A
    tasks

        Args:
            vocabulary (torch Vocab):
                the torch vocabulary
            tokenizer (torch Tokenizer):
                the tokenizer of the source text

    """

    def __init__(self, vocabulary=None,
                 tokenizer=None):
        if tokenizer is None:
            self.tokenizer = get_tokenizer('basic_english')
        else:
            self.tokenizer = tokenizer
        self.vocabulary = vocabulary
        self.MAX_LENGTH = 0
        self.is_fitted = False

    def fit_to_data(self, data):

        counter = Counter()  # for the text
        for (context, question, answer, init_position) in data:
            if isinstance(context, tuple) or isinstance(context, list):
                context = context[0]
            counter.update(self.tokenizer(context))
            self.MAX_LENGTH = max(self.MAX_LENGTH, len(self.tokenizer(context)))
            if isinstance(question, tuple) or isinstance(question, list):
                question = question[0]
            counter.update(self.tokenizer(question))
            self.MAX_LENGTH = max(self.MAX_LENGTH, len(self.tokenizer(question)))
            #if isinstance(answer, tuple) or isinstance(answer, list):
            #    answer = answer[0]
            #    if isinstance(answer, tuple) or isinstance(answer, list):
            #        answer = answer[0]
            #counter.update(self.tokenizer(answer))
            #self.MAX_LENGTH_ANSWER = max(self.MAX_LENGTH_ANSWER, len(self.tokenizer(answer)))
        if self.vocabulary is None:
            self.vocabulary = Vocab(counter)
        self.pad_item = self.vocabulary["."]
        self.is_fitted = False
        self.save_pretrained(".")

    def transform(self, datum):
        if not self.is_fitted:
            self.load_pretrained(".")
        text_pipeline = lambda x: [self.vocabulary[token] for token in
                                   self.tokenizer(x)]

        processed_context = torch.tensor(text_pipeline(datum[0]),
                                      dtype=torch.int64).to(DEVICE)
        out_context = torch.cat([processed_context,
                         self.pad_item * torch.ones(self.MAX_LENGTH - processed_context.shape[0]
                                               ).to(DEVICE)])
        processed_question = torch.tensor(text_pipeline(datum[1]),
                                         dtype=torch.int64).to(DEVICE)

        out_question = torch.cat([processed_question,
                         self.pad_item * torch.ones(self.MAX_LENGTH - processed_question.shape[0]
                                               ).to(DEVICE)])

        return out_context, out_question


class PreprocessTextQATarget(AbstractPreprocessing):
    """Class to preprocess text dataloaders for Q&A
    tasks

        Args:
            vocabulary (torch Vocab):
                the torch vocabulary
            tokenizer (torch Tokenizer):
                the tokenizer of the source text

    """

    def __init__(self, tokenizer=None):
        if tokenizer is None:
            self.tokenizer = get_tokenizer('basic_english')
        else:
            self.tokenizer = tokenizer
        self.MAX_LENGTH = 0
        self.is_fitted = False

    def fit_to_data(self, data):
        pass

    def transform(self, datum):

        pos_init_char = datum[3][0]
        pos_init = len(self.tokenizer(datum[0][:pos_init_char]))
        pos_end = pos_init + len(self.tokenizer(datum[2][0]))

        return torch.tensor(pos_init, dtype=torch.long), torch.tensor(pos_end, dtype=torch.long)
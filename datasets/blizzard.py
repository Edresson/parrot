import os

from fuel.schemes import ConstantScheme, ShuffledExampleScheme
from fuel.transformers import (
    AgnosticSourcewiseTransformer, Batch, FilterSources, ForceFloatX,
    Mapping, Padding, ScaleAndShift, SortMapping, Unpack)
from fuel.streams import DataStream

from play.toy.segment_transformer import SegmentSequence
from play.datasets.blizzard import Blizzard

import numpy

from utils import (
    mean_f0, mean_mgc, mean_spectrum, std_f0, std_mgc, std_spectrum)


def _length(data):
    return len(data[0])


def _remove_nans(data):
    if numpy.any(numpy.isnan(data[-1])):
        f0, mgc, spectrum, transcripts, voicing_str = data
        idx = numpy.where(numpy.any(numpy.isnan(voicing_str), axis=1))[0][0]
        data = tuple([f0[:idx], mgc[:idx], spectrum[:idx],
                      transcripts, voicing_str[:idx]])
    return data


def _equalize_length(data):
    f0, f0_mask, mgc, spectrum, \
        transcripts, transcripts_mask, voicing_str = data
    idx = int(f0_mask[0].sum())
    return tuple([f0[:, :idx], f0_mask[:, :idx], mgc[:, :idx],
                  spectrum[:, :idx], transcripts, transcripts_mask,
                  voicing_str[:, :idx]])


def _transpose(data):
    return data.swapaxes(0, 1)


def _is_nonzero(data):
    return tuple([1. * (data[0] > 0)])


def _zero_for_unvoiced(data):
    # Multiply by 0 the unvoiced components. HARDCODED
    return tuple((data[0] * data[-1],) + data[1:])


def _clip_f0(data, ceil=300.):
    temp_var = data[0]
    temp_var[temp_var > ceil] = ceil
    return tuple((temp_var,) + data[1:])

data_dir = os.environ['FUEL_DATA_PATH']
data_dir = os.path.join(data_dir, 'blizzard/', 'full_standardize.npz')
data_stats = numpy.load(data_dir)


class SourceMapping(AgnosticSourcewiseTransformer):
    """Apply a function to a subset of sources.
    Similar to the Mapping transformer but for a subset of sources.
    It will apply the same function to each source.
    Parameters
    ----------
    mapping : callable
    """

    def __init__(self, data_stream, mapping, **kwargs):
        """Initialization.
        Parameters:
            data_stream: DataStream
            mapping: callable object
        """
        self.mapping = mapping
        if data_stream.axis_labels:
            kwargs.setdefault('axis_labels', data_stream.axis_labels.copy())
        super(SourceMapping, self).__init__(
            data_stream, data_stream.produces_examples, **kwargs)

    def transform_any_source(self, source_data, _):
        return numpy.asarray(self.mapping(source_data))


def blizzard_stream(which_sets=('train',), batch_size=64,
                    seq_length=100, num_examples=None, sorting_mult=20,
                    which_sources=None):

    all_sources = ('f0', 'f0_mask', 'mgc', 'spectrum',
                   'transcripts', 'transcripts_mask', 'voicing_str',)

    if not which_sources:
        # which_sources = ('f0', 'spectrum', 'start_flag', 'voiced')
        which_sources = ('f0', 'f0_mask', 'spectrum', 'start_flag',
                         'voiced', 'transcripts', 'transcripts_mask')

    dataset = Blizzard(
        which_sets=which_sets, filename="mgc_blizzard_sentence.hdf5")
    sorting_size = batch_size * sorting_mult

    if not num_examples:
        num_examples = sorting_size * (dataset.num_examples / sorting_size)

    data_stream = DataStream(
        dataset, iteration_scheme=ShuffledExampleScheme(num_examples))

    data_stream = Mapping(data_stream, _remove_nans)
    data_stream = Mapping(data_stream, _clip_f0)

    # epoch_iterator = data_stream.get_epoch_iterator()

    # means = []
    # stds = []
    # for i in range(num_examples):
    #     print i
    #     f0, mgc, spectrum, transcripts, voicing_str = next(epoch_iterator)

    #     means.append([
    #         f0[f0>0].mean(),
    #         mgc.mean(axis=0),
    #         spectrum.mean(axis=0),
    #         voicing_str.mean(axis=0)
    #         ])

    #     stds.append([
    #         f0[f0>0].std(),
    #         mgc.std(axis=0),
    #         spectrum.std(axis=0),
    #         voicing_str.std(axis=0)
    #         ])

    # means = [numpy.array(mean_).mean(axis = 0) for mean_ in zip(*means)]
    # stds = [numpy.array(std_).mean(axis = 0) for std_ in zip(*stds)]

    # mean_f0, mean_mgc, mean_spectrum, mean_voicing_str = means
    # std_f0, std_mgc, std_spectrum, std_voicing_str = stds

    # data_dir = os.environ['FUEL_DATA_PATH']
    # data_dir = os.path.join(data_dir, 'blizzard/', 'full_standardize.npz')

    # numpy.savez(data_dir,
    #     mean_f0 = mean_f0,
    #     mean_mgc = mean_mgc,
    #     mean_spectrum = mean_spectrum,
    #     mean_voicing_str = mean_voicing_str,
    #     std_f0 = std_f0,
    #     std_mgc = std_mgc,
    #     std_spectrum = std_spectrum,
    #     std_voicing_str = std_voicing_str)

    data_stream = Batch(
        data_stream, iteration_scheme=ConstantScheme(sorting_size))
    data_stream = Mapping(data_stream, SortMapping(_length))
    data_stream = Unpack(data_stream)
    data_stream = Batch(
        data_stream, iteration_scheme=ConstantScheme(batch_size))

    # Cut all sequences to the shape of the smallest one in the batch.
    # So we will not need masks.

    data_stream = Padding(data_stream)
    data_stream = FilterSources(data_stream, all_sources)
    data_stream = Mapping(data_stream, _equalize_length)
    data_stream = FilterSources(
        data_stream,
        ('f0', 'f0_mask', 'mgc', 'spectrum', 'transcripts',
         'transcripts_mask', 'voicing_str'))
    data_stream = SourceMapping(
        data_stream, _transpose,
        which_sources=('f0', 'f0_mask', 'mgc', 'spectrum', 'voicing_str'))
    data_stream = SegmentSequence(
        data_stream,
        seq_length + 1,
        return_last=False,
        which_sources=('f0', 'f0_mask', 'mgc', 'spectrum', 'voicing_str'),
        add_flag=True,
        share_value=True)

    data_stream = Mapping(
        data_stream, _is_nonzero, add_sources=('voiced',))

    data_stream = ScaleAndShift(
        data_stream,
        scale=1 / std_spectrum,
        shift=-mean_spectrum / std_spectrum,
        which_sources=('spectrum',))

    data_stream = ScaleAndShift(
        data_stream,
        scale=1 / std_f0,
        shift=-mean_f0 / std_f0,
        which_sources=('f0',))

    data_stream = ScaleAndShift(
        data_stream,
        scale=1 / std_mgc,
        shift=-mean_mgc / std_mgc,
        which_sources=('mgc',))

    data_stream = Mapping(data_stream, _zero_for_unvoiced)
    data_stream = ForceFloatX(
        data_stream,
        which_sources=('f0', 'f0_mask', 'spectrum', 'start_flag',
                       'voiced', 'transcripts_mask'))

    data_stream = FilterSources(data_stream, which_sources)
    return data_stream

if __name__ == "__main__":
    train_stream = blizzard_stream(batch_size=5, sorting_mult=2)
    print next(train_stream.get_epoch_iterator())

# maxs = []
# percs = []
# epoch_iterator = train_stream.get_epoch_iterator()
# for i in range(1000):
#     f0 = next(epoch_iterator)[0]
#     m = f0.max()
#     p = numpy.percentile(f0[f0>0],99)
#     print i, m, p
#     maxs.append(m)
#     percs.append(p)
# 600 seems like a good number to cut f0
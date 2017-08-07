import os
import json
import numpy as np
import tensorflow as tf
from trainer.volume_metrics import dc, hd, assd
import pydensecrf.densecrf as dcrf
from pydensecrf.utils import create_pairwise_gaussian, create_pairwise_bilateral, unary_from_softmax
import nibabel as nib
import cPickle as pickle


def export_data():
    predictions_dir = 'train_predictions'
    image_dir = 'Training'

    for file_path in os.listdir(predictions_dir):
        name, prediction, probability = read_prediction_file(os.path.join(predictions_dir, file_path))

        # build a .nii image
        img = nib.Nifti1Image(prediction, np.eye(4))
        img.set_data_dtype(dtype=np.uint8)

        path = os.path.join(image_dir, name)

        adc_name = next(l for l in os.listdir(path) if 'MR_ADC' in l)
        export_image = nib.load(os.path.join(image_dir, name, adc_name, adc_name + '.nii'))

        i = export_image.get_data()
        i[:] = img.get_data()

        # set name to specification and export
        _id = next(l for l in os.listdir(path) if 'MR_MTT' in l).split('.')[-1]
        export_path = os.path.join('export_files', 'SMIR.' + name + '.' + _id + '.nii')
        nib.save(export_image, os.path.join(export_path))

    print('Finished exporting')


def adjust_with_crf(probability, image, sdims, schan):

    crf = dcrf.DenseCRF(np.prod(probability.shape), 2)
    #crf = dcrf.DenseCRF(np.prod(probability.shape), 1)

    binary_prob = np.stack((1 - probability, probability), axis=0)
    unary = unary_from_softmax(binary_prob)
    #unary = unary_from_softmax(np.expand_dims(probability, axis=0))
    crf.setUnaryEnergy(unary)

    # # per dimension scale factors
    sdims = [sdims] * 3
    # # per channel scale factors
    schan = [schan] * 6


    # # per dimension scale factors
    # sdims = [3] * 3
    # # per channel scale factors
    # schan = [1] * 6

    smooth = create_pairwise_gaussian(sdims=sdims, shape=probability.shape)
    appearance = create_pairwise_bilateral(sdims=sdims, schan=schan, img=image, chdim=3)
    crf.addPairwiseEnergy(smooth, compat=2)
    crf.addPairwiseEnergy(appearance, compat=2)

    # 5 iterations
    result = crf.inference(2)

    crf_prediction = np.argmax(result, axis=0).reshape(probability.shape).astype(np.float32)

    return crf_prediction


def read_prediction_file(file_path):
    with open(file_path) as json_data:
        d = json.load(json_data)
    return d['name'], np.array(d['prediction']), np.array(d['probability'])


def get_original_image(image_path, is_training_data=False):
    record = tf.python_io.tf_record_iterator(image_path).next()
    example = tf.train.Example()
    example.ParseFromString(record)

    shape = np.fromstring(example.features.feature['shape'].bytes_list.value[0], dtype=np.int32)
    image = np.fromstring(example.features.feature['img_raw'].bytes_list.value[0], dtype=np.float32)
    image = image.reshape(shape)

    if is_training_data:
        ground_truth = np.fromstring(example.features.feature['gt_raw'].bytes_list.value[0], dtype=np.uint8)
        ground_truth = ground_truth.reshape(shape[:-1])
    else:
        ground_truth = None

    return image, ground_truth


def adjust_training_data(sdims, schan):
    predictions_dir = 'training_predictions'
    image_dir = 'isles_tfrecords'

    metrics = {outer_key: {inner_key: [] for inner_key in ['pre_crf', 'post_crf']} for outer_key in ['dc', 'hd', 'assd']}

    for file_path in os.listdir(predictions_dir):
        name, prediction, probability = read_prediction_file(os.path.join(predictions_dir, file_path))
        image, ground_truth = get_original_image(os.path.join(image_dir, name+'.tfrecord'), True)
        print(name)

        metrics['dc']['pre_crf'].append(dc(prediction, ground_truth))
        metrics['hd']['pre_crf'].append(hd(prediction, ground_truth))
        metrics['assd']['pre_crf'].append(assd(prediction, ground_truth))

        crf_prediction = adjust_with_crf(probability, image, sdims, schan)

        metrics['dc']['post_crf'].append(dc(crf_prediction, ground_truth))
        metrics['hd']['post_crf'].append(hd(crf_prediction, ground_truth))
        metrics['assd']['post_crf'].append(assd(crf_prediction, ground_truth))

    return metrics


def report_metric(pre_crf, post_crf):
    for name, fn in zip(['Mean', 'Standard Deviation', 'Maximum', 'Minimum'], [np.mean, np.std, np.max, np.min]):
        pre = fn(pre_crf)
        post = fn(post_crf)
        print('\t{0}'.format(name))
        print('\t\tpre crf: {0:.3f} \t post crf {1:.3f} \t change: {2:.3f}%'.format(pre, post, (post-pre)/pre*100))

sdims_values = [.5, .75, 1, 2, 5, 10, 30]
schan_values = [.5, .75, 1, 2, 5, 10, 30]

output = []
for sdims in sdims_values:
    for schan in schan_values:
        m = adjust_training_data(sdims, schan)
        for key, metric in m.iteritems():
            names = {'dc': 'Dice Coefficient', 'hd': 'Hausdorff Distance', 'assd': 'Average Symmetric Surface Distance'}
            print(' ')
            print(names[key])
            report_metric(metric['pre_crf'], metric['post_crf'])
        output.append(m)
        break

with open('output.pickle', 'w') as f:
    pickle.dump(output, f)

#export_data()

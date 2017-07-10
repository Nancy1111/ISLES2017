import argparse
import json
import os
import tensorflow as tf
from six.moves import cPickle as pickle
from tensorflow.python.lib.io import file_io
import model

tf.logging.set_verbosity(tf.logging.INFO)


def load_data(file_path):
    with file_io.FileIO(file_path, 'r') as f:
        data = pickle.load(f)
    return data['images'], data['ground_truth']


def run(target, is_chief, train_steps, job_dir, file_path):
    images, ground_truth = load_data(file_path[0])
    num_channels = images[0].shape[-1]
    hooks = []
    # Create a new graph and specify that as default
    with tf.Graph().as_default():
        with tf.device(tf.train.replica_device_setter()):
            # Returns the training graph and global step tensor
            train_op, global_step_tensor = model.model_fn(num_channels)

        # Creates a MonitoredSession for training
        # MonitoredSession is a Session-like object that handles
        # initialization, recovery and hooks
        # https://www.tensorflow.org/api_docs/python/tf/train/MonitoredTrainingSession
        with tf.train.MonitoredTrainingSession(master=target,
                                               is_chief=is_chief,
                                               checkpoint_dir=job_dir,
                                               hooks=hooks,
                                               save_checkpoint_secs=60*15,
                                               save_summaries_steps=1) as session:
            # Global step to keep track of global number of steps particularly in
            # distributed setting
            # step = global_step_tensor.eval(session=session)

            # give some random tensors because of feed_dict
            feed_dict = {'tf_input_data:0': images[0], 'tf_ground_truth:0': ground_truth[0]}
            step = session.run(global_step_tensor, feed_dict=feed_dict)

            # Run the training graph which returns the step number as tracked by
            # the global step tensor.
            # When train epochs is reached, session.should_stop() will be true. does nothing without queues
            while (train_steps is None or step < train_steps) and not session.should_stop():
                pos = step % (len(images) - 1)
                feed_dict = {'tf_input_data:0': images[pos], 'tf_ground_truth:0': ground_truth[pos]}
                step, _ = session.run([global_step_tensor, train_op], feed_dict=feed_dict)

                if step % 1 == 0:
                    tf.logging.info('Step: {0}'.format(step))


def dispatch(*args, **kwargs):
    """Parse TF_CONFIG to cluster_spec and call run() method
  TF_CONFIG environment variable is available when running using
  gcloud either locally or on cloud. It has all the information required
  to create a ClusterSpec which is important for running distributed code.
  """

    tf_config = os.environ.get('TF_CONFIG')

    # If TF_CONFIG is not available run local
    if not tf_config:
        return run('', True, *args, **kwargs)

    tf_config_json = json.loads(tf_config)

    cluster = tf_config_json.get('cluster')
    job_name = tf_config_json.get('task', {}).get('type')
    task_index = tf_config_json.get('task', {}).get('index')

    # If cluster information is empty run local
    if job_name is None or task_index is None:
        return run('', True, *args, **kwargs)

    cluster_spec = tf.train.ClusterSpec(cluster)
    server = tf.train.Server(cluster_spec,
                             job_name=job_name,
                             task_index=task_index)

    # Wait for incoming connections forever
    # Worker ships the graph to the ps server
    # The ps server manages the parameters of the model.
    #
    # See a detailed video on distributed TensorFlow
    # https://www.youtube.com/watch?v=la_M6bCV91M
    if job_name == 'ps':
        server.join()
        return
    elif job_name in ['master', 'worker']:
        return run(server.target, job_name == 'master', *args, **kwargs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--file-path',
                        required=True,
                        type=str,
                        help='Input files local or GCS', nargs='+')
    parser.add_argument('--job-dir',
                        required=True,
                        type=str,
                        help="""\
                      GCS or local dir for checkpoints, exports, and
                      summaries. Use an existing directory to load a
                      trained model, or a new directory to retrain""")
    parser.add_argument('--train-steps',
                        type=int,
                        help='Maximum number of training steps to perform.')
    parser.add_argument('--verbosity',
                        choices=[
                            'DEBUG',
                            'ERROR',
                            'FATAL',
                            'INFO',
                            'WARN'
                        ],
                        default='INFO',
                        help='Set logging verbosity')
    parse_args, unknown = parser.parse_known_args()
    # Set python level verbosity
    tf.logging.set_verbosity(parse_args.verbosity)
    # Set C++ Graph Execution level verbosity
    os.environ['TF_CPP_MIN_LOG_LEVEL'] = str(
        tf.logging.__dict__[parse_args.verbosity] / 10)
    del parse_args.verbosity

    if unknown:
        tf.logging.warn('Unknown arguments: {}'.format(unknown))

    dispatch(**parse_args.__dict__)
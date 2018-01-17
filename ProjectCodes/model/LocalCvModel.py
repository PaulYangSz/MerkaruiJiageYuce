#!/usr/bin/env python
# encoding: utf-8

"""
Use sklearn based API model to local run and tuning.
"""


import pandas as pd
import numpy as np
import time
from sklearn.linear_model import LinearRegression
from sklearn.base import BaseEstimator, RegressorMixin, ClassifierMixin
from sklearn.model_selection import GridSearchCV
from sklearn.model_selection import StratifiedKFold
from sklearn.utils.estimator_checks import check_estimator
from sklearn.utils.validation import check_X_y, check_array, check_is_fitted
from keras.layers import Input, Dropout, Dense, BatchNormalization, \
    Activation, concatenate, GRU, Embedding, Flatten
from keras.models import Model
from keras.callbacks import ModelCheckpoint, Callback, EarlyStopping#, TensorBoard
from keras import backend as K
from keras import optimizers
import logging
import logging.config

from ProjectCodes.model.DataReader import DataReader


def start_logging():
    # 加载前面的标准配置
    from ProjectCodes.logging_config import ConfigLogginfDict
    logging.config.dictConfig(ConfigLogginfDict(__file__).LOGGING)
    # 获取loggers其中的一个日志管理器
    logger = logging.getLogger("default")
    logger.info('\n\n#################\n~~~~~~Start~~~~~~\n#################')
    print(type(logger))
    return logger
if 'Logger' not in dir():
    Logger = start_logging()


class LocalRegressor(BaseEstimator, RegressorMixin):
    """ An sklearn-API regressor.
    Model 1: Embedding GRU ---- Embedding(text or cat) -> Concat[GRU(words) or Flatten(cat_vector)] ->  Dense -> Output
    Parameters
    ----------
    demo_param : All tuning parameters should be set in __init__()
        A parameter used for demonstation of how to pass and store paramters.
    Attributes
    ----------
    X_ : array, shape = [n_samples, n_features]
        The input passed during :meth:`fit`
    y_ : array, shape = [n_samples]
        The labels passed during :meth:`fit`
    """

    def __init__(self, data_reader:DataReader, name_emb_dim=20, item_desc_emb_dim=60, cat_name_emb_dim=20, brand_emb_dim=10,
                 cat_main_emb_dim=10, cat_sub_emb_dim=10, cat_sub2_emb_dim=10, item_cond_id_emb_dim=5,
                 GRU_layers_out_dim=(8, 16, 8), drop_out_layers=(0.25, 0.1), dense_layers_dim=(128, 64),
                 epochs=3, batch_size=512*3, lr_init=0.015, lr_final=0.007):
        self.data_reader = data_reader
        self.name_emb_dim = name_emb_dim
        self.item_desc_emb_dim = item_desc_emb_dim
        self.cat_name_emb_dim = cat_name_emb_dim
        self.brand_emb_dim = brand_emb_dim
        self.cat_main_emb_dim = cat_main_emb_dim
        self.cat_sub_emb_dim = cat_sub_emb_dim
        self.cat_sub2_emb_dim = cat_sub2_emb_dim
        self.item_cond_id_emb_dim = item_cond_id_emb_dim
        self.GRU_layers_out_dim = GRU_layers_out_dim
        assert len(drop_out_layers) == len(dense_layers_dim)
        self.drop_out_layers = drop_out_layers
        self.dense_layers_dim = dense_layers_dim
        self.emb_GRU_model = self.get_GRU_model(data_reader)
        self.epochs = epochs
        self.batch_size = batch_size
        self.lr_init = lr_init
        self.lr_final = lr_final

    def get_GRU_model(self, reader:DataReader):
        # Inputs
        name = Input(shape=[reader.name_seq_len], name="name")
        item_desc = Input(shape=[reader.item_desc_seq_len], name="item_desc")
        category_name = Input(shape=[reader.cat_name_seq_len], name="category_name")
        item_condition = Input(shape=[1], name="item_condition")
        category_main = Input(shape=[1], name="category_main")
        category_sub = Input(shape=[1], name="category_sub")
        category_sub2 = Input(shape=[1], name="category_sub2")
        brand = Input(shape=[1], name="brand")
        num_vars = Input(shape=[1], name="num_vars")

        # Embedding的作用是配置字典size和词向量len后，根据call参数的indices，返回词向量.
        #  类似TF的embedding_lookup
        #  name.shape=[None, MAX_NAME_SEQ] -> emb_name.shape=[None, MAX_NAME_SEQ, output_dim]
        emb_name = Embedding(input_dim=reader.n_text_dict_words, output_dim=self.name_emb_dim)(name)
        emb_item_desc = Embedding(reader.n_text_dict_words, self.item_desc_emb_dim)(item_desc)  # [None, MAX_ITEM_DESC_SEQ, emb_size]
        emb_category_name = Embedding(reader.n_text_dict_words, self.cat_name_emb_dim)(category_name)
        emb_cond_id = Embedding(reader.n_condition_id, self.item_cond_id_emb_dim)(item_condition)
        emb_cat_main = Embedding(reader.n_cat_main, self.cat_main_emb_dim)(category_main)
        emb_cat_sub = Embedding(reader.n_cat_sub, self.cat_sub_emb_dim)(category_sub)
        emb_cat_sub2 = Embedding(reader.n_cat_sub2, self.cat_sub2_emb_dim)(category_sub2)
        emb_brand = Embedding(reader.n_brand, self.brand_emb_dim)(brand)

        # GRU是配置一个cell输出的units长度后，根据call词向量入参,输出最后一个GRU cell的输出(因为默认return_sequences=False)
        rnn_layer_name = GRU(units=self.GRU_layers_out_dim[0])(emb_name)
        rnn_layer_item_desc = GRU(units=self.GRU_layers_out_dim[1])(emb_item_desc)  # rnn_layer_item_desc.shape=[None, 16]
        rnn_layer_cat_name = GRU(units=self.GRU_layers_out_dim[2])(emb_category_name)

        # main layer
        # 连接列表中的Tensor，按照axis组成一个大的Tensor
        main_layer = concatenate([Flatten()(emb_brand),  # [None, 1, 10] -> [None, 10]
                                  Flatten()(emb_cat_main),
                                  Flatten()(emb_cat_sub),
                                  Flatten()(emb_cat_sub2),
                                  Flatten()(emb_cond_id),
                                  rnn_layer_name,
                                  rnn_layer_item_desc,
                                  rnn_layer_cat_name,
                                  num_vars])
        # Concat[all] -> Dense1 -> ... -> DenseN
        for i in range(len(self.dense_layers_dim)):
            main_layer = Dropout(self.drop_out_layers[i])(Dense(self.dense_layers_dim[i], activation='relu')(main_layer))

        # output
        output = Dense(1, activation="linear")(main_layer)

        # model
        model = Model(inputs=[name, item_desc, brand, category_main, category_sub, category_sub2, category_name, item_condition, num_vars],
                      outputs=output)
        # optimizer = optimizers.RMSprop()
        optimizer = optimizers.Adam()
        model.compile(loss="mse", optimizer=optimizer)
        return model

    def fit(self, X, y):
        """A reference implementation of a fitting function for a regressor.
        Parameters
        ----------
        X : array-like, shape = [n_samples, n_features]
            The training input samples.
        y : array-like, shape = [n_samples]
            The target values. An array of float.
        Returns
        -------
        self : object
            Returns self.
        """
        # Check that X and y have correct shape
        X, y = check_X_y(X, y)

        self.X_ = X
        self.y_ = y

        # FITTING THE MODEL
        steps = int(X.shape()[0] / self.batch_size) * self.epochs
        # final_lr=init_lr * (1/(1+decay))**(steps-1)
        exp_decay = lambda init, final, step_num: (init / final) ** (1 / (step_num - 1)) - 1
        lr_decay = exp_decay(self.lr_init, self.lr_final, steps)
        log_subdir = '_'.join(['ep', str(self.epochs),
                               'bs', str(self.batch_size),
                               'lrI', str(self.lr_init),
                               'lrF', str(self.lr_final)])
        K.set_value(self.emb_GRU_model.optimizer.lr, self.lr_init)
        K.set_value(self.emb_GRU_model.optimizer.decay, lr_decay)

        print('~~~~~~~~~~~~In fit() type(X): {}'.format(type(X)))
        keras_X = self.data_reader.get_keras_data(X)
        history = self.emb_GRU_model.fit(keras_X, y, epochs=self.epochs, batch_size=self.batch_size, validation_split=0.01,
                                         # callbacks=[TensorBoard('./logs/'+log_subdir)],
                                         verbose=10)

        # Return the regressor
        return self

    def predict(self, X):
        """ A reference implementation of a prediction for a regressor.
        Parameters
        ----------
        X : array-like of shape = [n_samples, n_features]
            The input samples.
        Returns
        -------
        y : array of int of shape = [n_samples]
            The label for each sample is the label of the closest sample
            seen udring fit.
        """
        # Check is fit had been called
        check_is_fitted(self, ['X_', 'y_'])

        # Input validation
        X = check_array(X)

        keras_X = self.data_reader.get_keras_data(X)
        return self.emb_GRU_model.predict(keras_X, batch_size=self.batch_size)


class CvGridParams(object):
    scoring = 'neg_mean_squared_error'  # 'r2'
    rand_state = 20180117

    def __init__(self, param_type:str='default'):
        if param_type == 'default':
            self.name = param_type
            self.all_params = {
                'name_emb_dim': [20],
                'item_desc_emb_dim': [60],  # float, Penalty parameter C of the error term.
                'cat_name_emb_dim': [20],  # 'linear', 'poly', 'rbf', 'sigmoid', 'precomputed'
                'brand_emb_dim': [10],
                'cat_main_emb_dim': [10],  # Whether to enable probability estimates.
                'cat_sub_emb_dim': [10],  # 'ovo', 'ovr' or None
                'cat_sub2_emb_dim': [10],
                'item_cond_id_emb_dim': [5],
                'GRU_layers_out_dim': [(8, 16, 8)],
                'drop_out_layers': [(0.25, 0.1)],
                'dense_layers_dim': [(128, 64)],
                'epochs': [3],
                'batch_size': [512*3],
                'lr_init': [0.015],
                'lr_final': [0.007],
            }
        else:
            print("Construct CvGridParams with error param_type: " + param_type)


def print_param(cv_grid_params:CvGridParams):
    Logger.info('选取的模型参数为：')
    Logger.info("param_name = '{}'".format(cv_grid_params.name))
    Logger.info("regression loss = {}".format(cv_grid_params.scoring))
    Logger.info("rand_state = {}".format(cv_grid_params.rand_state))
    Logger.info("param_dict = {")
    search_param_list = []
    for k, v in cv_grid_params.all_params.items():
        Logger.info("\t'{}' = {}".format(k, v))
        if len(v) > 1:
            search_param_list.append(k)
    Logger.info("}")
    return search_param_list


def train_model_with_gridsearch(regress_model, sample_df, cv_grid_params):
    sample_X = sample_df.drop('target', axis=1)
    sample_y = sample_df['target']

    # Check the list of available parameters with `estimator.get_params().keys()`
    print("keys are:::: {}".format(regress_model.get_params().keys()))

    clf = GridSearchCV(estimator=regress_model,
                       param_grid=cv_grid_params.all_params,
                       n_jobs=1,
                       cv=StratifiedKFold(n_splits=3, shuffle=True, random_state=cv_grid_params.rand_state),
                       scoring=cv_grid_params.scoring,
                       verbose=2,
                       refit=True)
    clf.fit(sample_X, sample_y)
    return clf


if __name__ == "__main__":
    start_time = time.time()
    # 1. Get sample and last validation data.
    # Get Data include some pre-process.
    # Initial get fillna dataframe
    data_reader = DataReader(local_flag=True, cat_fill_type='fill_paulnull', brand_fill_type='fill_paulnull', item_desc_fill_type='fill_')
    Logger.info('[{}] Finished handling missing data...'.format(time.time() - start_time))

    # PROCESS CATEGORICAL DATA
    # TODO: 需要改变下分类规则然后重新编码尝试结果
    Logger.info("Handling categorical variables...")
    data_reader.le_encode()
    Logger.info('[{}] Finished PROCESSING CATEGORICAL DATA...'.format(time.time() - start_time))
    with pd.option_context('display.max_rows', None, 'display.max_columns', None, 'display.width', None,
                           'display.height', None):
        Logger.info(data_reader.train_df.head(3))

    # PROCESS TEXT: RAW
    Logger.info("Text to seq process...")
    Logger.info("   Fitting tokenizer...")
    data_reader.tokenizer_text_col()
    with pd.option_context('display.max_rows', None, 'display.max_columns', None, 'display.width', None,
                           'display.height', None):
        Logger.info(data_reader.train_df.head(3))
    Logger.info('[{}] Finished PROCESSING TEXT DATA...'.format(time.time() - start_time))

    # EMBEDDINGS MAX VALUE
    # Base on the histograms, we select the next lengths
    # TODO: TimeSteps的长度是否需要改变
    data_reader.ensure_fixed_value()
    Logger.info('[{}] Finished EMBEDDINGS MAX VALUE...'.format(time.time() - start_time))

    # EXTRACT DEVELOPMENT TEST
    sample_df, last_valida_df, test_df = data_reader.split_get_train_validation()
    print(sample_df.shape)
    print(last_valida_df.shape)

    # 2. Check self-made estimator
    # check_estimator(LocalRegressor)  # Can not pass because need default DataReader in __init__.

    # 3. Parameters of GridSearchCV use.
    cv_grid_params = CvGridParams()
    adjust_para_list = print_param(cv_grid_params)

    # 4. Use GridSearchCV to tuning model.
    regress_model = LocalRegressor(data_reader=data_reader)
    print('Begin to train self-defined sklearn-API regressor.')
    reg = train_model_with_gridsearch(regress_model, sample_df, cv_grid_params)




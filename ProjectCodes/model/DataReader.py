#!/usr/bin/env python
# encoding: utf-8

"""
Read data and do some pre-process.
"""

import pandas as pd
import re
import logging
import logging.config


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


def get_brand_top_cat0_info_df(df_source:pd.DataFrame):
    """
    brand -> [top_cat0_name, count]
    :param df_source: train_df + test_df
    """
    df_source_have_cat = df_source[~df_source.category_name.isnull()].copy()

    def split_cat_name(name, str_class):
        sub_array = name.split('/')
        if str_class == 'main':
            return sub_array[0]
        elif str_class == 'sub':
            return sub_array[1]
        else:
            return '/'.join(sub_array[2:])
    df_source_have_cat.loc[:, 'cat_main'] = df_source_have_cat['category_name'].map(lambda x: split_cat_name(x, 'main'))
    group_brand = df_source_have_cat['cat_main'].groupby(df_source_have_cat['brand_name'])
    top_cat_main_ser = group_brand.apply(lambda x: x.value_counts().index[0])
    top_cat_main_ser.name = 'top_cat_main'
    top_cat_main_count_ser = group_brand.apply(lambda x: x.value_counts().iloc[0])
    top_cat_main_count_ser.name = 'item_count'
    index_brand = df_source_have_cat['brand_name'].value_counts().index
    ret_brand_top_cat0_info_df = pd.DataFrame({'top_cat_main': top_cat_main_ser, 'item_count': top_cat_main_count_ser},
                                              index=index_brand,
                                              columns=['top_cat_main', 'item_count'])
    return ret_brand_top_cat0_info_df


def base_other_cols_get_brand(brand_known_ordered_list:list, brand_top_cat0_info_df:pd.DataFrame, row_ser:pd.Series):
    """
    通过前面的数据分析可以看到，name是不为空的，所以首先查看name中是否包含brand信息，找到匹配的brand集合
    其次使用item_description信息来缩小上述brand集合
    再次使用cat信息来看对应哪个brand最可能在这个cat上
    :param brand_known_ordered_list: 按照商品个数有序的品牌list
    :param brand_top_cat0_info_df: brand对应top1主类别和item数目
    """
    if pd.isnull(row_ser['brand_name']):
        name = row_ser['name']
        brand_in_name = list()
        for brand in brand_known_ordered_list:
            brand_finder = re.compile(r'\b' + brand + r'\b', re.I)
            if brand_finder.search(name):
                brand_in_name.append(brand)
        if len(brand_in_name) == 1:
            return brand_in_name[0]
        else:
            desc = row_ser['item_description']
            brand_in_desc = list()
            if not pd.isnull(desc):
                for brand in brand_known_ordered_list:
                    brand_finder = re.compile(r'\b' + brand + r'\b', re.I)
                    if brand_finder.search(desc):
                        brand_in_desc.append(brand)

        if len(brand_in_name) == 0:
            brand_select = brand_in_desc
        else:
            if len(brand_in_desc) == 0:
                brand_select = brand_in_name
            else:
                brand_inter = [brand_ for brand_ in brand_in_name if brand_ in brand_in_desc]
                brand_select = brand_inter if len(brand_inter) > 0 else brand_in_name

        if len(brand_select) == 1:
            return brand_select[0]
        elif len(brand_select) == 0:
            return 'paulnull'
        else:
            if pd.isnull(row_ser['category_name']):
                return brand_select[0]
            else:
                max_count = 0
                ret_brand = ''
                cat_main = row_ser['category_name'].split('/')[0]
                for brand in brand_select:
                    if brand_top_cat0_info_df.loc[brand, 'top_cat_main'] == cat_main:
                        this_count = brand_top_cat0_info_df.loc[brand, 'item_count']
                        if this_count >= max_count:
                            max_count = this_count
                            ret_brand = brand
                if max_count == 0:
                    return 'paulnull'
                else:
                    return ret_brand
    else:
        return row_ser['brand_name']


class DataReader():
    def __init__(self, local_flag:bool, cat_split_flag:bool, cat_fill_type:str, brand_fill_type:str, item_desc_fill_type:str):
        Logger.info('\n构建数据DF时使用的参数：\n'
                    'local_flag={}, '.format(local_flag, ))
        TRAIN_FILE = "../input/train.tsv"
        TEST_FILE = "../input/test.tsv"

        if local_flag:
            train_df = pd.read_csv("../" + TRAIN_FILE, sep='\t', engine='python')
            test_df = pd.read_csv("../" + TEST_FILE, sep='\t', engine='python')
        else:
            train_df = pd.read_csv(TRAIN_FILE, sep='\t')
            test_df = pd.read_csv(TEST_FILE, sep='\t')

        def fill_item_description_null(str_desc, replace):
            if pd.isnull(str_desc):
                return replace
            else:
                no_mean = re.compile(r"(No description yet|No description|\[rm\])", re.I)
                left = re.sub(pattern=no_mean, repl='', string=str_desc)
                if len(left) > 2:
                    return left
                else:
                    return replace
        if item_desc_fill_type == 'fill_':
            train_df.loc[:, 'item_description'] = train_df['item_description'].map(lambda x: fill_item_description_null(x, ''))
            test_df.loc[:, 'item_description'] = test_df['item_description'].map(lambda x: fill_item_description_null(x, ''))
        elif item_desc_fill_type == 'fill_paulnull':
            train_df.loc[:, 'item_description'] = train_df['item_description'].map(lambda x: fill_item_description_null(x, 'paulnull'))
            test_df.loc[:, 'item_description'] = test_df['item_description'].map(lambda x: fill_item_description_null(x, 'paulnull'))
        elif item_desc_fill_type == 'base_name':
            train_df.loc[:, 'item_description'] = train_df[['item_description', 'name']].apply(lambda x: fill_item_description_null(x.iloc[0], x.iloc[1]), axis=1)
            test_df.loc[:, 'item_description'] = test_df['item_description', 'name'].apply(lambda x: fill_item_description_null(x.iloc[0], x.iloc[1]), axis=1)
        else:
            print('【错误】：item_desc_fill_type shoulde be: "fill_" or "fill_paulnull" or "base_name"')

        if brand_fill_type == 'fill_paulnull':
            train_df['brand_name'].fillna(value="paulnull", inplace=True)
            test_df['brand_name'].fillna(value="paulnull", inplace=True)
        elif brand_fill_type == 'base_other_cols':



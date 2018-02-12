import numpy as np
import pandas as pd

from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import train_test_split
from sklearn.linear_model import Ridge
from sklearn.pipeline import FeatureUnion
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer

from keras.preprocessing.text import Tokenizer
from keras.preprocessing.sequence import pad_sequences
from keras.layers import Input, Dropout, Dense, concatenate, GRU, Embedding, Flatten, Activation
from keras.optimizers import Adam
from keras.models import Model
from keras import backend as K
import re

def rmsle(Y, Y_pred):
    # Y and Y_red have already been in log scale.
    assert Y.shape == Y_pred.shape
    return np.sqrt(np.mean(np.square(Y_pred - Y )))

train_df = pd.read_table('../input/train.tsv')
test_df = pd.read_table('../input/test.tsv')
print(train_df.shape, test_df.shape)


# drop price < 3
train_df = train_df[train_df['price'] >= 3]


def fill_missing_values(df):
    df.category_name.fillna(value="Other", inplace=True)
    df.brand_name.fillna(value="missing", inplace=True)
    df.item_description.fillna(value="None", inplace=True)
    return df

train_df = fill_missing_values(train_df)
test_df = fill_missing_values(test_df)


# 统计下description中特殊字符的个数
npc_patten = re.compile(r'!')  # '!+'
def patten_count(text, patten_):
    try:
        # text = text.lower()
        return len(patten_.findall(text))
    except:
        return 0
train_df['desc_npc_cnt'] = train_df['item_description'].map(lambda x: patten_count(x, npc_patten))
test_df['desc_npc_cnt'] = test_df['item_description'].map(lambda x: patten_count(x, npc_patten))


# Scale target variable to log.
train_df["target"] = np.log1p(train_df.price)

# Split training examples into train/dev examples.
train_df, dev_df = train_test_split(train_df, random_state=347, test_size=0.01)

Y_train = train_df.target.values.reshape(-11, 1)
Y_dev = dev_df.target.values.reshape(-1, 1)

# Calculate number of train/dev/test examples.
n_trains = train_df.shape[0]
n_devs = dev_df.shape[0]
n_tests = test_df.shape[0]
print("Training on", n_trains, "examples")
print("Validating on", n_devs, "examples")
print("Testing on", n_tests, "examples")


# Concatenate train - dev - test data for easy to handle
full_df = pd.concat([train_df, dev_df, test_df])


print("Processing categorical data...")
le = LabelEncoder()

le.fit(full_df.category_name)
full_df.category_name = le.transform(full_df.category_name)

le.fit(full_df.brand_name)
full_df.brand_name = le.transform(full_df.brand_name)

del le


print("Transforming text data to sequences...")
name_raw_text = full_df.name.str.lower()
desc_raw_text = full_df.item_description.str.lower()

print("   Fitting tokenizer...")
name_tok_raw = Tokenizer(num_words=250000)
desc_tok_raw = Tokenizer(num_words=600000)
name_tok_raw.fit_on_texts(name_raw_text)
desc_tok_raw.fit_on_texts(desc_raw_text)

print("   Transforming text to sequences...")
full_df['seq_item_description'] = desc_tok_raw.texts_to_sequences(full_df.item_description.str.lower())
full_df['desc_len'] = full_df['seq_item_description'].map(len)
full_df['seq_name'] = name_tok_raw.texts_to_sequences(full_df.name.str.lower())



# Define constants to use when define RNN model
MAX_NAME_SEQ = 10
MAX_ITEM_DESC_SEQ = 75
MAX_NAME_TEXT = min(max(name_tok_raw.word_index.values()), name_tok_raw.num_words) + 2
MAX_DESC_TEXT = min(max(desc_tok_raw.word_index.values()), desc_tok_raw.num_words) + 2
del name_tok_raw, desc_tok_raw
MAX_CATEGORY = np.max(full_df.category_name.max()) + 1
MAX_BRAND = np.max(full_df.brand_name.max()) + 1
MAX_CONDITION = np.max(full_df.item_condition_id.max()) + 1
MAX_DESC_LEN = np.max(full_df.desc_len.max()) + 1
MAX_NPC_CNT = np.max(full_df.desc_npc_cnt.max()) + 1


def get_keras_data(df):
    X = {
        'name': pad_sequences(df.seq_name, maxlen=MAX_NAME_SEQ),
        'item_desc': pad_sequences(df.seq_item_description, maxlen=MAX_ITEM_DESC_SEQ),
        'brand_name': np.array(df.brand_name),
        'category_name': np.array(df.category_name),
        'item_condition': np.array(df.item_condition_id),
        'desc_len': np.array(df.desc_len),
        'desc_npc_cnt': np.array(df.desc_npc_cnt),
        'num_vars': np.array(df[["shipping"]]),
    }
    return X

train = full_df[:n_trains]
dev = full_df[n_trains:n_trains+n_devs]
test = full_df[n_trains+n_devs:]

X_train = get_keras_data(train)
X_dev = get_keras_data(dev)
X_test = get_keras_data(test)


def new_rnn_model(lr=0.001, decay=0.0):
    # Inputs
    name = Input(shape=[X_train["name"].shape[1]], name="name")
    item_desc = Input(shape=[X_train["item_desc"].shape[1]], name="item_desc")
    brand_name = Input(shape=[1], name="brand_name")
    category_name = Input(shape=[1], name="category_name")
    item_condition = Input(shape=[1], name="item_condition")
    desc_len = Input(shape=[1], name="desc_len")
    desc_npc_cnt = Input(shape=[1], name="desc_npc_cnt")
    num_vars = Input(shape=[X_train["num_vars"].shape[1]], name="num_vars")

    # Embeddings layers
    emb_name = Embedding(MAX_NAME_TEXT, 20)(name)
    emb_item_desc = Embedding(MAX_DESC_TEXT, 60)(item_desc)
    emb_brand_name = Embedding(MAX_BRAND, 10)(brand_name)
    emb_category_name = Embedding(MAX_CATEGORY, 10)(category_name)
    emb_desc_len = Embedding(MAX_CATEGORY, 7)(desc_len)
    emb_desc_npc_cnt = Embedding(MAX_NPC_CNT, 7)(desc_npc_cnt)

    # rnn layers
    rnn_layer1 = GRU(16) (emb_item_desc)
    rnn_layer2 = GRU(8) (emb_name)

    # main layers
    main_l = concatenate([
        Flatten() (emb_brand_name),
        Flatten() (emb_category_name),
        Flatten() (emb_desc_len),
        Flatten() (emb_desc_npc_cnt),
        item_condition,
        rnn_layer1,
        rnn_layer2,
        num_vars,
    ])

    main_l = Dense(256)(main_l)
    main_l = Activation('elu')(main_l)

    main_l = Dense(128)(main_l)
    main_l = Activation('elu')(main_l)

    main_l = Dense(64)(main_l)
    main_l = Activation('elu')(main_l)

    # the output layer.
    output = Dense(1, activation="linear") (main_l)

    model = Model([name, item_desc, brand_name , category_name, item_condition, desc_len, desc_npc_cnt, num_vars], output)

    optimizer = Adam(lr=lr, decay=decay)
    model.compile(loss="mse", optimizer=optimizer)

    return model

model = new_rnn_model()
model.summary()
del model


# Set hyper parameters for the model.
BATCH_SIZE = 1024
epochs = 2

# Calculate learning rate decay.
exp_decay = lambda init, fin, steps: (init/fin)**(1/(steps-1)) - 1
steps = int(n_trains / BATCH_SIZE) * epochs
lr_init, lr_fin = 0.007, 0.0005
lr_decay = exp_decay(lr_init, lr_fin, steps)

rnn_model = new_rnn_model(lr=lr_init, decay=lr_decay)

print("Fitting RNN model to training examples...")
rnn_model.fit(
        X_train, Y_train, epochs=epochs, batch_size=BATCH_SIZE,
        validation_data=(X_dev, Y_dev), verbose=10,
)


print("Evaluating the model on validation data...")
Y_dev_preds_rnn = rnn_model.predict(X_dev, batch_size=BATCH_SIZE)
print(" RMSLE error:", rmsle(Y_dev, Y_dev_preds_rnn))


rnn_preds = rnn_model.predict(X_test, batch_size=BATCH_SIZE, verbose=10)
rnn_preds = np.expm1(rnn_preds)



# Concatenate train - dev - test data for easy to handle
full_df = pd.concat([train_df, dev_df, test_df])

# Convert data type to string
full_df['shipping'] = full_df['shipping'].astype(str)
full_df['item_condition_id'] = full_df['item_condition_id'].astype(str)


print("Vectorizing data...")
default_preprocessor = CountVectorizer().build_preprocessor()
def build_preprocessor(field):
    field_idx = list(full_df.columns).index(field)
    return lambda x: default_preprocessor(x[field_idx])

vectorizer = FeatureUnion([
    ('name', CountVectorizer(
        ngram_range=(1, 2),
        max_features=50000,
        preprocessor=build_preprocessor('name'))),
    ('category_name', CountVectorizer(
        token_pattern='.+',
        preprocessor=build_preprocessor('category_name'))),
    ('brand_name', CountVectorizer(
        token_pattern='.+',
        preprocessor=build_preprocessor('brand_name'))),
    ('shipping', CountVectorizer(
        token_pattern='\d+',
        preprocessor=build_preprocessor('shipping'))),
    ('item_condition_id', CountVectorizer(
        token_pattern='\d+',
        preprocessor=build_preprocessor('item_condition_id'))),
    ('item_description', TfidfVectorizer(
        ngram_range=(1, 3),
        max_features=100000,
        preprocessor=build_preprocessor('item_description'))),
])

X = vectorizer.fit_transform(full_df.values)

X_train = X[:n_trains]
X_dev = X[n_trains:n_trains+n_devs]
X_test = X[n_trains+n_devs:]

print(X.shape, X_train.shape, X_dev.shape, X_test.shape)


print("Fitting Ridge model on training examples...")
ridge_model = Ridge(
    solver='auto', fit_intercept=True, alpha=0.5,
    max_iter=100, normalize=False, tol=0.05,
)
ridge_model.fit(X_train, Y_train)


Y_dev_preds_ridge = ridge_model.predict(X_dev)
Y_dev_preds_ridge = Y_dev_preds_ridge.reshape(-1, 1)
print("RMSL error on dev set:", rmsle(Y_dev, Y_dev_preds_ridge))


ridge_preds = ridge_model.predict(X_test)
ridge_preds = np.expm1(ridge_preds)


def aggregate_predicts(Y1, Y2):
    assert Y1.shape == Y2.shape
    ratio = 0.63
    return Y1 * ratio + Y2 * (1.0 - ratio)

Y_dev_preds = aggregate_predicts(Y_dev_preds_rnn, Y_dev_preds_ridge)
print("RMSL error for RNN + Ridge on dev set:", rmsle(Y_dev, Y_dev_preds))


preds = aggregate_predicts(rnn_preds, ridge_preds)
preds[preds < 3] = 3
submission = pd.DataFrame({
        "test_id": test_df.test_id,
        "price": preds.reshape(-1),
})
submission.to_csv("./rnn_ridge_submission.csv", index=False)



